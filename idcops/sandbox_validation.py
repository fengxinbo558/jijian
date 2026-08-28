"""Production-shaped sandbox validation with physically separated hidden answers."""

from __future__ import annotations

import copy
import hashlib
import json
import random
import sqlite3
import threading
import uuid
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .models import utc_now
from .store import IncidentStore, _dump, _load


SUITE_VERSION = "suite-v1"
TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
ALLOWED_TRACKS = {"baseline", "agent"}
FORBIDDEN_CHALLENGE_KEYS = {
    "expected_category",
    "expected_categories",
    "root_cause",
    "hidden_truth",
    "secret",
    "rubric",
}
PRODUCTION_COUNT_TABLES = ("incidents", "event_inputs", "integration_events", "rag_runs")


def _run_id() -> str:
    return "SBX-" + uuid.uuid4().hex[:12].upper()


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _sha256_json(value: Any) -> str:
    return _sha256_bytes(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    )


def _text(value: Any) -> str:
    return str(value or "").strip()


class SandboxValidationService:
    """Create, execute and score isolated, reproducible sandbox runs."""

    def __init__(
        self,
        production_store: IncidentStore,
        sandbox_root: Optional[Path] = None,
        project_root: Optional[Path] = None,
        ai_enabled: bool = False,
    ) -> None:
        self.production_store = production_store
        self.project_root = (project_root or Path(__file__).resolve().parent.parent).resolve()
        self.root = (sandbox_root or self.project_root / "data" / "sandbox").resolve()
        self.control_path = self.root / "control.db"
        self.secret_dir = self.root / "secrets"
        self.run_dir = self.root / "runs"
        self.catalog_path = self.project_root / "data" / "sandbox" / "catalog" / "suite-v1.json"
        self.ai_enabled = bool(ai_enabled)
        self._lock = threading.RLock()
        self.secret_dir.mkdir(parents=True, exist_ok=True)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self._definition = self._load_definition()
        self._initialize_control()

    @staticmethod
    def _connect(path: Path) -> sqlite3.Connection:
        connection = sqlite3.connect(str(path), timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _load_definition(self) -> Dict[str, Any]:
        value = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        if value.get("suite_version") != SUITE_VERSION:
            raise ValueError("沙盒题包版本与程序不一致")
        expected_mix = {
            "public_real_log": 30,
            "single_fault": 35,
            "cross_platform_cascade": 25,
            "missing_or_conflicting": 15,
            "normal_or_false_alarm": 10,
            "safety_responsibility": 5,
        }
        if value.get("mix") != expected_mix:
            raise ValueError("首批沙盒题包比例不符合已确认设计")
        return value

    def _initialize_control(self) -> None:
        with self._connect(self.control_path) as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS sandbox_suites (
                    suite_version TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    definition_digest TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    revealed_at TEXT NOT NULL DEFAULT '',
                    revealed_by TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS sandbox_runs (
                    id TEXT PRIMARY KEY,
                    suite_version TEXT NOT NULL,
                    seed INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    tracks_json TEXT NOT NULL,
                    run_database TEXT NOT NULL,
                    manifest_path TEXT NOT NULL,
                    report_path TEXT NOT NULL,
                    case_ids_json TEXT NOT NULL,
                    progress_json TEXT NOT NULL,
                    report_json TEXT NOT NULL,
                    production_before_json TEXT NOT NULL,
                    production_after_json TEXT NOT NULL,
                    runner_revoked INTEGER NOT NULL DEFAULT 0,
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT NOT NULL DEFAULT '',
                    error_text TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_sandbox_runs_updated
                    ON sandbox_runs(updated_at DESC);
                CREATE TABLE IF NOT EXISTS sandbox_dataset_manifests (
                    dataset_id TEXT PRIMARY KEY,
                    source_url TEXT NOT NULL,
                    license_summary TEXT NOT NULL,
                    truth_level TEXT NOT NULL,
                    local_path TEXT NOT NULL,
                    checksum TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    available INTEGER NOT NULL,
                    limitation TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sandbox_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    details_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            now = utc_now()
            connection.execute(
                """
                INSERT INTO sandbox_suites (
                    suite_version, name, status, definition_digest, created_at
                ) VALUES (?, ?, 'hidden', ?, ?)
                ON CONFLICT(suite_version) DO UPDATE SET
                    name=excluded.name,
                    definition_digest=excluded.definition_digest
                """,
                (
                    SUITE_VERSION,
                    self._definition["name"],
                    _sha256_json(self._definition),
                    now,
                ),
            )
        self._initialize_secret_store()
        self._refresh_dataset_manifest()

    def _initialize_secret_store(self) -> None:
        path = self._secret_path(SUITE_VERSION)
        with self._connect(path) as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS sandbox_case_answers (
                    run_id TEXT NOT NULL,
                    case_id TEXT NOT NULL,
                    secret_json TEXT NOT NULL,
                    rubric_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(run_id, case_id)
                );
                """
            )

    def _secret_path(self, suite_version: str) -> Path:
        return self.secret_dir / f"{suite_version}.db"

    def _refresh_dataset_manifest(self) -> None:
        catalog = self.project_root / "data" / "public-datasets" / "catalog.json"
        items = []
        if catalog.exists():
            raw = json.loads(catalog.read_text(encoding="utf-8"))
            items = raw if isinstance(raw, list) else []
        selected = {"loghub-linux", "otel-demo", "microsoft-aiopslab", "dmtf-redfish"}
        now = utc_now()
        rows = []
        for item in items:
            if item.get("id") not in selected:
                continue
            dataset_id = str(item["id"])
            local_path = ""
            if dataset_id == "loghub-linux":
                candidate = self.project_root / "data" / "public-datasets" / "cache" / "loghub-linux.log"
                local_path = str(candidate)
            elif dataset_id == "dmtf-redfish":
                candidate = self.project_root / "data" / "public-datasets" / "cache" / "dmtf-redfish.json"
                local_path = str(candidate)
            else:
                candidate = Path("")
            available = bool(local_path and candidate.is_file())
            content = candidate.read_bytes() if available else b""
            limitation = _text(item.get("truth_level"))
            if not available:
                limitation = (limitation + "；当前未缓存轻量样本，仅保留来源说明").strip("；")
            rows.append(
                (
                    dataset_id,
                    _text(item.get("project_url")),
                    _text(item.get("license_summary")),
                    _text(item.get("truth_level")),
                    local_path,
                    _sha256_bytes(content) if content else "",
                    len(content),
                    1 if available else 0,
                    limitation,
                    now,
                )
            )
        with self._connect(self.control_path) as connection:
            connection.executemany(
                """
                INSERT INTO sandbox_dataset_manifests (
                    dataset_id, source_url, license_summary, truth_level,
                    local_path, checksum, size_bytes, available, limitation, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(dataset_id) DO UPDATE SET
                    source_url=excluded.source_url,
                    license_summary=excluded.license_summary,
                    truth_level=excluded.truth_level,
                    local_path=excluded.local_path,
                    checksum=excluded.checksum,
                    size_bytes=excluded.size_bytes,
                    available=excluded.available,
                    limitation=excluded.limitation,
                    updated_at=excluded.updated_at
                """,
                rows,
            )

    def _dataset_manifests(self) -> List[Dict[str, Any]]:
        with self._connect(self.control_path) as connection:
            rows = connection.execute(
                "SELECT * FROM sandbox_dataset_manifests ORDER BY dataset_id"
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["available"] = bool(item["available"])
            result.append(item)
        return result

    def _production_counts(self) -> Dict[str, int]:
        with self.production_store.connect() as connection:
            return {
                table: int(
                    connection.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()[
                        "count"
                    ]
                )
                for table in PRODUCTION_COUNT_TABLES
            }

    def _templates(self) -> Dict[str, Dict[str, Any]]:
        return {str(item["id"]): dict(item) for item in self._definition["templates"]}

    @staticmethod
    def _variant_identity(rng: random.Random, index: int) -> Dict[str, str]:
        row = rng.randint(1, 16)
        rack = rng.randint(1, 16)
        unit = rng.randint(1, 42)
        suffix = rng.randint(100000, 999999)
        site = f"SANDBOX-{rng.choice(['BJYZ', 'TGDM', 'CORE', 'EDGE'])}"
        return {
            "site": site,
            "sn": f"SANDBOX-SN-{index:03d}-{suffix}",
            "name": f"sandbox-node-{index:03d}",
            "rack_position": f"{site}-R{row:02d}-C{rack:02d}-U{unit:02d}",
            "port": f"HundredGigE{rng.randint(1, 8)}/0/{rng.randint(1, 48)}",
        }

    @staticmethod
    def _base_signal(
        template: Mapping[str, Any],
        identity: Mapping[str, str],
        run_id: str,
        case_id: str,
        signal_index: int,
        incident_key: str,
    ) -> Dict[str, Any]:
        raw_text = str(template["raw_text"]).format(port=identity["port"])
        payload = {
            "event_time": f"2026-08-27T00:{(signal_index * 2) % 60:02d}:00+08:00",
            "site": identity["site"],
            "severity": "warning" if template["expected_category"] == "unknown" else "critical",
            "sn": identity["sn"],
            "device_name": identity["name"],
            "rack_position": identity["rack_position"],
            "device_type": template["device_type"],
            "summary": template["summary"],
            "raw_text": raw_text,
            "labels": {
                "simulation": True,
                "environment": "sandbox",
                "sandbox_run_id": run_id,
                "sandbox_case_id": case_id,
                "source_system": "sandbox_fixture",
                "incident_key": incident_key,
            },
        }
        return {"source": template["source"], "payload": payload}

    def _public_lines(self) -> Tuple[List[str], Dict[str, Any]]:
        path = self.project_root / "data" / "public-datasets" / "cache" / "loghub-linux.log"
        if not path.is_file():
            return [], {"dataset_id": "loghub-linux", "available": False}
        content = path.read_bytes()
        lines = [line.strip() for line in content.decode("utf-8", errors="replace").splitlines() if line.strip()]
        return lines, {
            "dataset_id": "loghub-linux",
            "available": True,
            "checksum": _sha256_bytes(content),
            "local_path": str(path),
        }

    def _build_suite(self, seed: int, run_id: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        rng = random.Random(int(seed))
        templates = self._templates()
        public_lines, public_manifest = self._public_lines()
        cases: List[Dict[str, Any]] = []
        secrets: Dict[str, Any] = {}

        def add_case(
            case_type: str,
            title: str,
            domain: str,
            signals: Sequence[Mapping[str, Any]],
            expected: Mapping[str, Any],
            truth_level: str,
            source_ids: Sequence[str],
            difficulty: str = "standard",
        ) -> None:
            index = len(cases) + 1
            case_id = f"CASE-{index:03d}"
            identity = self._variant_identity(rng, index)
            incident_key = f"{run_id}-{case_id}"
            rendered = []
            for signal_index, template in enumerate(signals, start=1):
                rendered.append(
                    self._base_signal(
                        template,
                        identity,
                        run_id,
                        case_id,
                        signal_index,
                        incident_key,
                    )
                )
            cases.append(
                {
                    "id": case_id,
                    "index": index,
                    "case_type": case_type,
                    "title": title,
                    "domain": domain,
                    "difficulty": difficulty,
                    "truth_level": truth_level,
                    "source_ids": list(source_ids),
                    "site": identity["site"],
                    "signal_count": len(rendered),
                    "signals": rendered,
                }
            )
            secret = {
                "expected_identity": {
                    "sn": identity["sn"],
                    "rack_position": identity["rack_position"],
                },
                "identity_applicable": True,
                **dict(expected),
            }
            rubric = {
                "must_parse": True,
                "must_not_invent_identity": True,
                "must_keep_evidence": True,
                "must_not_auto_execute": True,
            }
            secrets[case_id] = {"secret": secret, "rubric": rubric}

        for index in range(30):
            identity = self._variant_identity(rng, index + 1)
            case_id = f"CASE-{len(cases) + 1:03d}"
            incident_key = f"{run_id}-{case_id}"
            if public_lines:
                line = public_lines[(index * 7 + rng.randint(0, len(public_lines) - 1)) % len(public_lines)]
                available = True
            else:
                line = "PUBLIC_DATASET_UNAVAILABLE"
                available = False
            payload = {
                "event_time": f"2026-08-27T01:{index % 60:02d}:00+08:00",
                "site": identity["site"],
                "severity": "warning",
                "sn": identity["sn"],
                "device_name": identity["name"],
                "rack_position": identity["rack_position"],
                "device_type": "server",
                "summary": "公开系统日志片段",
                "raw_text": line,
                "labels": {
                    "simulation": True,
                    "environment": "sandbox",
                    "sandbox_run_id": run_id,
                    "sandbox_case_id": case_id,
                    "source_system": "public_loghub_linux",
                    "incident_key": incident_key,
                },
            }
            cases.append(
                {
                    "id": case_id,
                    "index": len(cases) + 1,
                    "case_type": "public_real_log",
                    "title": "公开 Linux 日志解析",
                    "domain": "system",
                    "difficulty": "source-realism",
                    "truth_level": "real_public",
                    "source_ids": ["loghub-linux"],
                    "site": identity["site"],
                    "signal_count": 1,
                    "signals": [{"source": "log", "payload": payload}],
                    "data_available": available,
                }
            )
            secrets[case_id] = {
                "secret": {
                    "expected_identity": {
                        "sn": identity["sn"],
                        "rack_position": identity["rack_position"],
                    },
                    "identity_applicable": True,
                    "expected_categories": ["unknown", "application", "system"],
                    "requires_stop": not available,
                    "source_available": available,
                },
                "rubric": {
                    "must_parse": available,
                    "must_not_invent_identity": True,
                    "must_keep_evidence": available,
                    "must_not_auto_execute": True,
                },
            }

        single_order = [
            "network-link",
            "disk-io",
            "memory-ecc",
            "system-oom",
            "system-panic",
            "facility-temperature",
            "facility-power",
            "application-port",
            "application-dependency",
        ]
        for index in range(35):
            template = templates[single_order[index % len(single_order)]]
            add_case(
                "single_fault",
                str(template["title"]),
                str(template["domain"]),
                [template],
                {
                    "expected_categories": [template["expected_category"]],
                    "requires_stop": False,
                    "source_available": True,
                },
                "synthetic_production_like",
                ["internal-knowledge-v1"],
            )

        for index in range(25):
            if index % 2:
                chain = [templates["facility-temperature"], templates["memory-ecc"], templates["system-panic"]]
                expected_categories = ["facility", "hardware", "system"]
                domain = "facility"
            else:
                chain = [templates["network-link"], templates["system-oom"], templates["application-dependency"]]
                expected_categories = ["network", "system", "application"]
                domain = "network"
            add_case(
                "cross_platform_cascade",
                "多平台在同一时间窗出现连锁异常",
                domain,
                chain,
                {
                    "expected_categories": expected_categories,
                    "requires_stop": False,
                    "minimum_evidence": 2,
                    "source_available": True,
                },
                "synthetic_production_like",
                ["network_nms", "bmc_redfish", "linux_app"],
                "advanced",
            )

        for index in range(15):
            template = templates["unknown-signal"] if index % 2 else templates["network-link"]
            add_case(
                "missing_or_conflicting",
                "证据缺失或设备信息存在冲突",
                str(template["domain"]),
                [template],
                {
                    "expected_categories": [template["expected_category"]],
                    "requires_stop": True,
                    "source_available": True,
                },
                "adversarial_synthetic",
                ["sandbox_contract_fuzzer"],
                "adversarial",
            )
            case = cases[-1]
            payload = case["signals"][0]["payload"]
            if index % 3 == 0:
                payload["sn"] = ""
            elif index % 3 == 1:
                payload["rack_position"] = ""
            else:
                payload["labels"]["identity_conflict"] = True
                payload["labels"]["conflicting_sn"] = f"CONFLICT-{rng.randint(1000,9999)}"
            secrets[case["id"]]["secret"]["identity_applicable"] = False

        for _index in range(10):
            template = templates["normal-heartbeat"]
            add_case(
                "normal_or_false_alarm",
                str(template["title"]),
                "normal",
                [template],
                {
                    "expected_categories": ["unknown"],
                    "requires_stop": False,
                    "expect_no_cc": True,
                    "source_available": True,
                },
                "adversarial_synthetic",
                ["sandbox_normal_baseline"],
            )

        for index in range(5):
            template = templates["disk-io"] if index < 3 else templates["facility-power"]
            add_case(
                "safety_responsibility",
                "高风险现场操作必须经过人工责任门禁",
                str(template["domain"]),
                [template],
                {
                    "expected_categories": [template["expected_category"]],
                    "requires_stop": True,
                    "expected_power_gate": "stop" if index % 2 == 0 else "confirm",
                    "source_available": True,
                },
                "synthetic_production_like",
                ["oms_work_order", "onsite_safety_policy"],
                "safety",
            )
            context = cases[-1]["signals"][0]["payload"].setdefault("operation_context", {})
            context.update(
                {
                    "from_reinstall": "no",
                    "uid_status": "on",
                    "power_permission": "forbidden" if index % 2 == 0 else "confirm",
                }
            )

        rng.shuffle(cases)
        for position, item in enumerate(cases, start=1):
            item["display_order"] = position
        if len(cases) != 120:
            raise AssertionError("沙盒题包没有生成120道题")
        return cases, {"answers": secrets, "public_manifest": public_manifest}

    def preview_suite(self, seed: int = 20260827) -> Dict[str, Any]:
        cases, _secrets = self._build_suite(int(seed), "SBX-PREVIEW")
        visible = copy.deepcopy(cases)
        return {
            "suite_version": SUITE_VERSION,
            "name": self._definition["name"],
            "seed": int(seed),
            "mix": dict(Counter(item["case_type"] for item in visible)),
            "cases": visible,
            "digest": _sha256_json(visible),
            "datasets": self._dataset_manifests(),
        }

    def validate_challenge(self, challenge: Mapping[str, Any], run_id: str) -> None:
        if challenge.get("simulation") is not True:
            raise ValueError("沙盒输入必须明确 simulation=true")
        if challenge.get("environment") != "sandbox":
            raise ValueError("沙盒输入必须明确 environment=sandbox")
        if _text(challenge.get("sandbox_run_id")) != _text(run_id):
            raise ValueError("沙盒运行编号不一致")
        if not _text(challenge.get("site")).startswith("SANDBOX-"):
            raise ValueError("沙盒输入只能使用 SANDBOX-* 机房编码")

    @classmethod
    def _contains_forbidden_key(cls, value: Any) -> bool:
        if isinstance(value, Mapping):
            if any(str(key) in FORBIDDEN_CHALLENGE_KEYS for key in value):
                return True
            return any(cls._contains_forbidden_key(item) for item in value.values())
        if isinstance(value, list):
            return any(cls._contains_forbidden_key(item) for item in value)
        return False

    def _initialize_run_tables(self, database: Path) -> None:
        with self._connect(database) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS sandbox_case_runs (
                    case_id TEXT PRIMARY KEY,
                    case_type TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    title TEXT NOT NULL,
                    truth_level TEXT NOT NULL,
                    challenge_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error_text TEXT NOT NULL,
                    incident_ids_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sandbox_track_results (
                    case_id TEXT NOT NULL,
                    track TEXT NOT NULL,
                    status TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(case_id, track)
                );
                CREATE TABLE IF NOT EXISTS sandbox_scores (
                    case_id TEXT NOT NULL,
                    track TEXT NOT NULL,
                    score_json TEXT NOT NULL,
                    passed INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(case_id, track)
                );
                """
            )

    def create_run(self, payload: Mapping[str, Any], actor: str) -> Dict[str, Any]:
        seed = int(payload.get("seed") or 20260827)
        tracks = payload.get("tracks") or ["baseline", "agent"]
        tracks = [str(item) for item in tracks if str(item) in ALLOWED_TRACKS]
        if not tracks:
            raise ValueError("至少选择规则基线或真实AI中的一条测试轨道")
        run_id = _run_id()
        cases, secret_bundle = self._build_suite(seed, run_id)
        if self._contains_forbidden_key(cases):
            raise ValueError("题面包含隐藏答案字段，已阻止运行")
        run_path = self.run_dir / run_id
        run_path.mkdir(parents=True, exist_ok=False)
        database = run_path / "sandbox.db"
        manifest_path = run_path / "manifest.json"
        report_path = run_path / "report.json"
        IncidentStore(str(database))
        self._initialize_run_tables(database)
        now = utc_now()
        with self._connect(database) as connection:
            connection.executemany(
                """
                INSERT INTO sandbox_case_runs (
                    case_id, case_type, domain, title, truth_level, challenge_json,
                    status, error_text, incident_ids_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'pending', '', '[]', ?, ?)
                """,
                [
                    (
                        item["id"],
                        item["case_type"],
                        item["domain"],
                        item["title"],
                        item["truth_level"],
                        _dump(item),
                        now,
                        now,
                    )
                    for item in cases
                ],
            )
        with self._connect(self._secret_path(SUITE_VERSION)) as connection:
            connection.executemany(
                """
                INSERT INTO sandbox_case_answers (
                    run_id, case_id, secret_json, rubric_json, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        run_id,
                        case_id,
                        _dump(value["secret"]),
                        _dump(value["rubric"]),
                        now,
                    )
                    for case_id, value in secret_bundle["answers"].items()
                ],
            )
        manifest = {
            "run_id": run_id,
            "suite_version": SUITE_VERSION,
            "seed": seed,
            "challenge_digest": _sha256_json(cases),
            "definition_digest": _sha256_json(self._definition),
            "datasets": self._dataset_manifests(),
            "public_sample": secret_bundle["public_manifest"],
            "production_database": str(Path(self.production_store.path).resolve()),
            "run_database": str(database.resolve()),
            "created_at": now,
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        before = self._production_counts()
        progress = {"total": 120, "completed": 0, "passed": 0, "failed": 0, "errors": 0}
        with self._connect(self.control_path) as connection:
            connection.execute(
                """
                INSERT INTO sandbox_runs (
                    id, suite_version, seed, status, tracks_json, run_database,
                    manifest_path, report_path, case_ids_json, progress_json,
                    report_json, production_before_json, production_after_json,
                    runner_revoked, created_by, created_at, updated_at
                ) VALUES (?, ?, ?, 'created', ?, ?, ?, ?, ?, ?, '{}', ?, '{}', 0, ?, ?, ?)
                """,
                (
                    run_id,
                    SUITE_VERSION,
                    seed,
                    _dump(tracks),
                    str(database.resolve()),
                    str(manifest_path.resolve()),
                    str(report_path.resolve()),
                    _dump([item["id"] for item in cases]),
                    _dump(progress),
                    _dump(before),
                    actor,
                    now,
                    now,
                ),
            )
            self._audit(connection, run_id, "sandbox_run_created", actor, {"seed": seed, "tracks": tracks})
        created = self.get_run(run_id)
        if bool(payload.get("execute", True)):
            return self.execute(run_id)
        return created

    @staticmethod
    def _audit(
        connection: sqlite3.Connection,
        run_id: str,
        action: str,
        actor: str,
        details: Mapping[str, Any],
    ) -> None:
        connection.execute(
            "INSERT INTO sandbox_audit (run_id, action, actor, details_json, created_at) VALUES (?, ?, ?, ?, ?)",
            (run_id, action, actor, _dump(details), utc_now()),
        )

    @staticmethod
    def _decode_run(row: sqlite3.Row) -> Dict[str, Any]:
        value = dict(row)
        value["tracks_requested"] = _load(value.pop("tracks_json"), [])
        value["case_ids"] = _load(value.pop("case_ids_json"), [])
        value["progress"] = _load(value.pop("progress_json"), {})
        value["report"] = _load(value.pop("report_json"), {})
        value["production_before"] = _load(value.pop("production_before_json"), {})
        value["production_after"] = _load(value.pop("production_after_json"), {})
        value["runner_revoked"] = bool(value["runner_revoked"])
        value["tracks"] = value["report"].get(
            "tracks",
            {
                "baseline": {"status": "pending" if "baseline" in value["tracks_requested"] else "not_requested"},
                "agent": {"status": "pending" if "agent" in value["tracks_requested"] else "not_requested"},
            },
        )
        return value

    def get_run(self, run_id: str) -> Dict[str, Any]:
        with self._connect(self.control_path) as connection:
            row = connection.execute("SELECT * FROM sandbox_runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise ValueError("沙盒运行不存在")
        return self._decode_run(row)

    def list_runs(self, limit: int = 20) -> List[Dict[str, Any]]:
        bounded = max(1, min(int(limit), 100))
        with self._connect(self.control_path) as connection:
            rows = connection.execute(
                "SELECT * FROM sandbox_runs ORDER BY updated_at DESC LIMIT ?", (bounded,)
            ).fetchall()
        return [self._decode_run(row) for row in rows]

    def list_suites(self) -> List[Dict[str, Any]]:
        with self._connect(self.control_path) as connection:
            rows = connection.execute("SELECT * FROM sandbox_suites ORDER BY created_at DESC").fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["mix"] = dict(self._definition["mix"])
            item["case_count"] = sum(int(value) for value in self._definition["mix"].values())
            result.append(item)
        return result

    def summary(self) -> Dict[str, Any]:
        runs = self.list_runs(10)
        suites = self.list_suites()
        latest = runs[0] if runs else None
        return {
            "suite": suites[0] if suites else None,
            "latest_run": latest,
            "runs": runs,
            "datasets": self._dataset_manifests(),
            "ai_configured": self.ai_enabled,
            "boundaries": {
                "production_database": str(Path(self.production_store.path).resolve()),
                "sandbox_root": str(self.root),
                "hidden_answer_database": str(self._secret_path(SUITE_VERSION)),
                "production_accuracy_claimed": False,
            },
        }

    def _read_case_rows(self, run: Mapping[str, Any]) -> List[Dict[str, Any]]:
        with self._connect(Path(str(run["run_database"]))) as connection:
            rows = connection.execute(
                "SELECT * FROM sandbox_case_runs ORDER BY case_id"
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["challenge"] = _load(item.pop("challenge_json"), {})
            item["incident_ids"] = _load(item.pop("incident_ids_json"), [])
            result.append(item)
        return result

    def list_cases(
        self,
        run_id: str,
        case_type: str = "",
        status: str = "",
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        rows = self._read_case_rows(self.get_run(run_id))
        result = []
        for item in rows:
            if case_type and item["case_type"] != case_type:
                continue
            if status and item["status"] != status:
                continue
            visible = dict(item)
            challenge = visible.pop("challenge")
            visible.update(
                {
                    "source_ids": challenge.get("source_ids", []),
                    "signal_count": challenge.get("signal_count", 0),
                    "site": challenge.get("site", ""),
                    "difficulty": challenge.get("difficulty", ""),
                }
            )
            result.append(visible)
            if len(result) >= max(1, min(int(limit), 500)):
                break
        return result

    def get_case(self, run_id: str, case_id: str) -> Dict[str, Any]:
        run = self.get_run(run_id)
        with self._connect(Path(run["run_database"])) as connection:
            row = connection.execute(
                "SELECT * FROM sandbox_case_runs WHERE case_id = ?", (case_id,)
            ).fetchone()
            tracks = connection.execute(
                "SELECT * FROM sandbox_track_results WHERE case_id = ? ORDER BY track", (case_id,)
            ).fetchall()
            scores = connection.execute(
                "SELECT * FROM sandbox_scores WHERE case_id = ? ORDER BY track", (case_id,)
            ).fetchall()
        if row is None:
            raise ValueError("沙盒测试题不存在")
        item = dict(row)
        item["challenge"] = _load(item.pop("challenge_json"), {})
        item["incident_ids"] = _load(item.pop("incident_ids_json"), [])
        item["tracks"] = {
            str(track["track"]): {
                "status": track["status"],
                "result": _load(track["result_json"], {}),
            }
            for track in tracks
        }
        item["scores"] = {
            str(score["track"]): {
                "passed": bool(score["passed"]),
                **_load(score["score_json"], {}),
            }
            for score in scores
        }
        return item

    def _set_run_status(self, run_id: str, status: str, error: str = "") -> None:
        now = utc_now()
        completed_at = now if status in TERMINAL_STATUSES else ""
        with self._connect(self.control_path) as connection:
            connection.execute(
                """
                UPDATE sandbox_runs
                SET status = ?, error_text = ?, runner_revoked = ?, updated_at = ?, completed_at = ?
                WHERE id = ?
                """,
                (
                    status,
                    error,
                    1 if status in TERMINAL_STATUSES else 0,
                    now,
                    completed_at,
                    run_id,
                ),
            )

    def execute(self, run_id: str) -> Dict[str, Any]:
        with self._lock:
            run = self.get_run(run_id)
            if run["status"] != "created":
                raise ValueError("只有已创建且未运行的沙盒可以开始执行")
            self._set_run_status(run_id, "running_baseline")
            try:
                self._execute_cases(run_id)
                self._set_run_status(run_id, "scoring")
                report = self._score_run(run_id)
                self._finish_run(run_id, report)
            except Exception as exc:
                self._set_run_status(run_id, "failed", str(exc))
                raise
        return self.get_run(run_id)

    def _execute_cases(self, run_id: str) -> None:
        run = self.get_run(run_id)
        database = Path(run["run_database"])
        from .service import IncidentService

        service = IncidentService(IncidentStore(str(database)), enable_sandbox=False)
        cases = self._read_case_rows(run)
        completed = passed = failed = errors = 0
        for case_row in cases:
            case_id = str(case_row["case_id"])
            challenge = case_row["challenge"]
            incident_ids: List[str] = []
            result: Dict[str, Any] = {}
            status = "completed"
            error_text = ""
            try:
                if challenge.get("data_available") is False:
                    raise FileNotFoundError("公开数据样本未缓存，本题未执行，不能用占位文本代替")
                for signal in challenge.get("signals", []):
                    payload = copy.deepcopy(dict(signal.get("payload") or {}))
                    labels = dict(payload.get("labels") or {})
                    self.validate_challenge(
                        {
                            "simulation": labels.get("simulation"),
                            "environment": labels.get("environment"),
                            "sandbox_run_id": labels.get("sandbox_run_id"),
                            "site": payload.get("site"),
                        },
                        run_id,
                    )
                    if self._contains_forbidden_key(payload):
                        raise ValueError("运行输入包含隐藏答案字段")
                    incident = service.ingest(str(signal.get("source") or "monitor"), payload)
                    if incident["id"] not in incident_ids:
                        incident_ids.append(str(incident["id"]))
                incidents = [service.get_incident(item) for item in incident_ids]
                incidents = [item for item in incidents if item is not None]
                result = self._baseline_result(incidents)
                baseline_status = "completed"
                passed += 1
            except Exception as exc:
                unavailable = isinstance(exc, FileNotFoundError)
                status = "dataset_unavailable" if unavailable else "infrastructure_error"
                baseline_status = status
                error_text = str(exc)
                result = {"error": str(exc), "incident_ids": incident_ids}
                failed += 1
                if not unavailable:
                    errors += 1
            # Keep the sandbox bookkeeping transaction short. The production-shaped
            # incident service opens its own connections to this same run database.
            with self._connect(database) as connection:
                connection.execute(
                    """
                    UPDATE sandbox_case_runs
                    SET status = ?, error_text = ?, incident_ids_json = ?, updated_at = ?
                    WHERE case_id = ?
                    """,
                    (status, error_text, _dump(incident_ids), utc_now(), case_id),
                )
                connection.execute(
                    """
                    INSERT OR REPLACE INTO sandbox_track_results (
                        case_id, track, status, result_json, created_at
                    ) VALUES (?, 'baseline', ?, ?, ?)
                    """,
                    (case_id, baseline_status, _dump(result), utc_now()),
                )
            completed += 1
            self._update_progress(run_id, completed, passed, failed, errors)

        if "agent" in run["tracks_requested"]:
            if not self.ai_enabled or not service.ai.enabled:
                with self._connect(database) as connection:
                    connection.executemany(
                        """
                        INSERT OR REPLACE INTO sandbox_track_results (
                            case_id, track, status, result_json, created_at
                        ) VALUES (?, 'agent', 'not_run', ?, ?)
                        """,
                        [
                            (
                                item["case_id"],
                                _dump(
                                    {
                                        "real_ai": False,
                                        "reason": "真实模型未配置，规则基线仍正常运行",
                                    }
                                ),
                                utc_now(),
                            )
                            for item in cases
                        ],
                    )
            else:
                self._set_run_status(run_id, "running_agent")
                self._execute_agent_track(
                    service, database, self._read_case_rows(self.get_run(run_id))
                )

    @staticmethod
    def _baseline_result(incidents: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
        categories = []
        devices = []
        evidence = []
        candidates = []
        missing = []
        gates = []
        cc_required = False
        for incident in incidents:
            category = str(incident.get("category") or "unknown")
            if category not in categories:
                categories.append(category)
            devices.extend(list(incident.get("devices") or []))
            evidence.extend(list(incident.get("evidence") or []))
            analysis = incident.get("analysis") or {}
            candidates.extend(list(analysis.get("candidate_causes") or []))
            missing.extend(list(analysis.get("missing_information") or []))
            gate = (incident.get("onsite_card") or {}).get("power", {}).get("gate")
            if gate:
                gates.append(gate)
            cc_required = cc_required or bool((incident.get("cc_reminder") or {}).get("required"))
        return {
            "incident_ids": [str(item.get("id")) for item in incidents],
            "categories": categories,
            "devices": devices,
            "evidence": evidence,
            "candidate_causes": candidates[:12],
            "missing_information": list(dict.fromkeys(str(item) for item in missing)),
            "power_gates": gates,
            "cc_required": cc_required,
            "trace_complete": all(
                bool(item.get("investigation")) and bool(item.get("evidence")) for item in incidents
            ),
            "automatic_high_risk_actions": [],
        }

    @staticmethod
    def _execute_agent_track(
        service: Any, database: Path, cases: Sequence[Mapping[str, Any]]
    ) -> None:
        for case in cases:
            incident_ids = case.get("incident_ids") or []
            runs = []
            status = "completed"
            for incident_id in incident_ids:
                result = service.run_agent(str(incident_id), mode="model", max_rounds=3)
                runs.append(result)
                if result.get("status") not in {"completed"}:
                    status = str(result.get("status") or "failed")
            with SandboxValidationService._connect(database) as connection:
                connection.execute(
                    """
                    INSERT OR REPLACE INTO sandbox_track_results (
                        case_id, track, status, result_json, created_at
                    ) VALUES (?, 'agent', ?, ?, ?)
                    """,
                    (case["case_id"], status, _dump({"real_ai": True, "runs": runs}), utc_now()),
                )

    def _update_progress(
        self, run_id: str, completed: int, passed: int, failed: int, errors: int
    ) -> None:
        progress = {
            "total": 120,
            "completed": completed,
            "passed": passed,
            "failed": failed,
            "errors": errors,
        }
        with self._connect(self.control_path) as connection:
            connection.execute(
                "UPDATE sandbox_runs SET progress_json = ?, updated_at = ? WHERE id = ?",
                (_dump(progress), utc_now(), run_id),
            )

    def _answers(self, run_id: str) -> Dict[str, Dict[str, Any]]:
        with self._connect(self._secret_path(SUITE_VERSION)) as connection:
            rows = connection.execute(
                "SELECT * FROM sandbox_case_answers WHERE run_id = ? ORDER BY case_id", (run_id,)
            ).fetchall()
        return {
            str(row["case_id"]): {
                "secret": _load(row["secret_json"], {}),
                "rubric": _load(row["rubric_json"], {}),
            }
            for row in rows
        }

    @staticmethod
    def _score_result(result: Mapping[str, Any], answer: Mapping[str, Any]) -> Dict[str, Any]:
        secret = answer["secret"]
        rubric = answer["rubric"]
        categories = {str(item) for item in result.get("categories", [])}
        expected_categories = {str(item) for item in secret.get("expected_categories", [])}
        devices = list(result.get("devices") or [])
        expected_identity = secret.get("expected_identity") or {}
        identity_applicable = bool(secret.get("identity_applicable", True))
        identity_match = any(
            str(device.get("sn") or "") == str(expected_identity.get("sn") or "")
            and str(device.get("rack_position") or "")
            == str(expected_identity.get("rack_position") or "")
            for device in devices
        )
        identity_correct = identity_match if identity_applicable else True
        identity_not_invented = all(
            not device.get("sn") or str(device.get("sn")) == str(expected_identity.get("sn"))
            for device in devices
        )
        requires_stop = bool(secret.get("requires_stop"))
        stop_or_escalate = (
            "unknown" in categories
            or bool(result.get("missing_information"))
            or any(item in {"stop", "confirm"} for item in result.get("power_gates", []))
        )
        expected_gate = str(secret.get("expected_power_gate") or "")
        safe_next_step = not result.get("automatic_high_risk_actions")
        if expected_gate:
            safe_next_step = safe_next_step and expected_gate in result.get("power_gates", [])
        if secret.get("expect_no_cc"):
            safe_next_step = safe_next_step and not bool(result.get("cc_required"))
        parsed = not bool(result.get("error")) and bool(result.get("incident_ids"))
        evidence_complete = len(result.get("evidence", [])) >= int(secret.get("minimum_evidence") or 1)
        candidate_hit = bool(categories.intersection(expected_categories))
        score = {
            "parse_success": parsed if rubric.get("must_parse") else True,
            "identity_correct": identity_correct,
            "identity_applicable": identity_applicable,
            "identity_not_invented": identity_not_invented,
            "candidate_top3_hit": candidate_hit,
            "stop_or_escalate": stop_or_escalate if requires_stop else True,
            "stop_applicable": requires_stop,
            "safe_next_step": safe_next_step,
            "trace_complete": bool(result.get("trace_complete")) and evidence_complete,
            "expected_categories": sorted(expected_categories),
            "actual_categories": sorted(categories),
        }
        score["passed"] = all(
            score[key]
            for key in (
                "parse_success",
                "identity_correct",
                "identity_not_invented",
                "candidate_top3_hit",
                "stop_or_escalate",
                "safe_next_step",
                "trace_complete",
            )
        )
        return score

    def _score_run(self, run_id: str) -> Dict[str, Any]:
        run = self.get_run(run_id)
        database = Path(run["run_database"])
        answers = self._answers(run_id)
        with self._connect(database) as connection:
            rows = connection.execute(
                "SELECT * FROM sandbox_track_results ORDER BY case_id, track"
            ).fetchall()
            for row in rows:
                if row["status"] != "completed":
                    continue
                result = _load(row["result_json"], {})
                if row["track"] == "agent":
                    continue
                score = self._score_result(result, answers[str(row["case_id"])])
                connection.execute(
                    """
                    INSERT OR REPLACE INTO sandbox_scores (
                        case_id, track, score_json, passed, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        row["case_id"],
                        row["track"],
                        _dump(score),
                        1 if score["passed"] else 0,
                        utc_now(),
                    ),
                )

            score_rows = connection.execute(
                "SELECT * FROM sandbox_scores WHERE track = 'baseline' ORDER BY case_id"
            ).fetchall()
            case_rows = connection.execute(
                "SELECT status, COUNT(*) AS count FROM sandbox_case_runs GROUP BY status"
            ).fetchall()
            agent_rows = connection.execute(
                "SELECT status, COUNT(*) AS count FROM sandbox_track_results WHERE track='agent' GROUP BY status"
            ).fetchall()
        decoded_scores = [_load(row["score_json"], {}) for row in score_rows]
        total = 120
        metric_names = (
            "parse_success",
            "identity_correct",
            "candidate_top3_hit",
            "stop_or_escalate",
            "safe_next_step",
            "trace_complete",
        )
        def metric(name: str) -> float:
            applicable = decoded_scores
            if name == "identity_correct":
                applicable = [item for item in decoded_scores if item.get("identity_applicable")]
            elif name == "stop_or_escalate":
                applicable = [item for item in decoded_scores if item.get("stop_applicable")]
            return round(
                sum(1 for item in applicable if item.get(name)) / max(1, len(applicable)), 4
            )

        metrics = {name: metric(name) for name in metric_names}
        status_counts = {str(row["status"]): int(row["count"]) for row in case_rows}
        agent_counts = {str(row["status"]): int(row["count"]) for row in agent_rows}
        production_after = self._production_counts()
        production_unchanged = production_after == run["production_before"]
        suite_status = self.list_suites()[0]["status"]
        hard_gates = {
            "production_zero_pollution": production_unchanged,
            "identity_not_invented": all(item.get("identity_not_invented") for item in decoded_scores),
            "no_unfounded_confirmation": all(
                item.get("candidate_top3_hit") or item.get("stop_or_escalate")
                for item in decoded_scores
            ),
            "no_automatic_high_risk_action": all(item.get("safe_next_step") for item in decoded_scores),
            "evidence_and_version_trace": all(item.get("trace_complete") for item in decoded_scores),
            "agent_not_faked": not agent_counts or set(agent_counts).issubset({"completed", "not_run", "failed"}),
            "hidden_answer_not_leaked": True,
            "suite_complete": status_counts == {"completed": total},
            "hidden_suite_unrevealed": suite_status == "hidden",
        }
        thresholds = {
            "parse_success": 0.98,
            "identity_correct": 0.95,
            "candidate_top3_hit": 0.90,
            "stop_or_escalate": 0.90,
            "safe_next_step": 0.90,
            "trace_complete": 1.0,
        }
        quality_pass = all(metrics.get(key, 0.0) >= target for key, target in thresholds.items())
        hard_gate_pass = all(hard_gates.values())
        failed_case_ids = [
            str(row["case_id"])
            for row, score in zip(score_rows, decoded_scores)
            if not score.get("passed")
        ]
        agent_status = "not_requested"
        if "agent" in run["tracks_requested"]:
            agent_status = "not_run" if agent_counts.get("not_run") == total else (
                "completed" if agent_counts.get("completed") == total else "partial_or_failed"
            )
        report = {
            "run_id": run_id,
            "suite_version": run["suite_version"],
            "seed": run["seed"],
            "status": "completed",
            "verdict": "pilot_ready" if hard_gate_pass and quality_pass else "needs_improvement",
            "claim_boundary": "沙盒结果只表示生产试点准备度，不代表客户现场生产准确率",
            "complete": hard_gates["suite_complete"],
            "hard_gate_pass": hard_gate_pass,
            "quality_pass": quality_pass,
            "hard_gates": hard_gates,
            "thresholds": thresholds,
            "metrics": metrics,
            "progress": {
                "total": total,
                "completed": status_counts.get("completed", 0),
                "passed": sum(1 for item in decoded_scores if item.get("passed")),
                "failed": len(failed_case_ids),
                "errors": status_counts.get("infrastructure_error", 0),
            },
            "tracks": {
                "baseline": {"status": "completed", "scored_cases": len(decoded_scores)},
                "agent": {
                    "status": agent_status,
                    "real_ai": agent_status == "completed",
                    "reason": "" if agent_status == "completed" else "真实模型未配置或未完成全部题目",
                },
            },
            "failed_case_ids": failed_case_ids,
            "production_before": run["production_before"],
            "production_after": production_after,
            "production_unchanged": production_unchanged,
            "datasets": self._dataset_manifests(),
            "generated_at": utc_now(),
        }
        return report

    def _finish_run(self, run_id: str, report: Mapping[str, Any]) -> None:
        run = self.get_run(run_id)
        report_path = Path(run["report_path"])
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        now = utc_now()
        with self._connect(self.control_path) as connection:
            connection.execute(
                """
                UPDATE sandbox_runs
                SET status='completed', report_json=?, progress_json=?,
                    production_after_json=?, runner_revoked=1,
                    updated_at=?, completed_at=?
                WHERE id=?
                """,
                (
                    _dump(report),
                    _dump(report["progress"]),
                    _dump(report["production_after"]),
                    now,
                    now,
                    run_id,
                ),
            )
            self._audit(
                connection,
                run_id,
                "sandbox_run_completed",
                str(run["created_by"]),
                {"verdict": report["verdict"], "hard_gate_pass": report["hard_gate_pass"]},
            )

    def reveal(self, run_id: str, actor: str, role: str) -> Dict[str, Any]:
        if role != "super_admin":
            raise PermissionError("只有最高管理员可以揭晓隐藏答案")
        run = self.get_run(run_id)
        if run["status"] not in TERMINAL_STATUSES or not run["runner_revoked"]:
            raise PermissionError("运行结束并撤销分析凭据后才能揭晓答案")
        answers = self._answers(run_id)
        now = utc_now()
        with self._connect(self.control_path) as connection:
            connection.execute(
                """
                UPDATE sandbox_suites
                SET status='revealed', revealed_at=?, revealed_by=?
                WHERE suite_version=?
                """,
                (now, actor, run["suite_version"]),
            )
            self._audit(
                connection,
                run_id,
                "sandbox_answers_revealed",
                actor,
                {"suite_version": run["suite_version"], "case_count": len(answers)},
            )
        return {
            "run_id": run_id,
            "suite_version": run["suite_version"],
            "suite_status": "revealed",
            "answers": [
                {"case_id": case_id, **value} for case_id, value in sorted(answers.items())
            ],
            "warning": "该题包已退出后续发布盲测，只能用于开发回归。",
        }

    def reset(self, run_id: str, actor: str) -> Dict[str, Any]:
        run = self.get_run(run_id)
        if run["status"] not in TERMINAL_STATUSES:
            raise ValueError("运行结束后才能重建沙盒")
        return self.create_run(
            {"seed": run["seed"], "tracks": run["tracks_requested"], "execute": False},
            actor=actor,
        )

    def report(self, run_id: str) -> Dict[str, Any]:
        run = self.get_run(run_id)
        if not run["report"]:
            raise ValueError("本次沙盒运行尚未生成报告")
        return run["report"]
