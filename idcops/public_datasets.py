"""License-aware public dataset catalog and bounded local sample importers."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import uuid
from pathlib import Path
from typing import Any, Dict, Mapping, Optional
from urllib.request import Request, urlopen

from .models import utc_now
from .production import ProductionGovernance
from .store import IncidentStore, _dump, _load


MAX_SAMPLE_BYTES = 2 * 1024 * 1024
ANOMALY_WORDS = (
    " error",
    "error ",
    "failed",
    "failure",
    "critical",
    "panic",
    "fatal",
    "segfault",
    "i/o error",
    "link down",
)


class PublicDatasetService:
    def __init__(
        self,
        store: IncidentStore,
        governance: ProductionGovernance,
        catalog_path: Optional[Path] = None,
        cache_dir: Optional[Path] = None,
    ) -> None:
        root = Path(__file__).resolve().parent.parent
        self.store = store
        self.governance = governance
        self.catalog_path = catalog_path or root / "data" / "public-datasets" / "catalog.json"
        self.cache_dir = cache_dir or root / "data" / "public-datasets" / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _catalog(self) -> list:
        data = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise ValueError("公开数据目录必须是数组")
        return [dict(item) for item in data if isinstance(item, Mapping)]

    def _definition(self, dataset_id: str) -> Dict[str, Any]:
        for item in self._catalog():
            if item.get("id") == dataset_id:
                return item
        raise ValueError("公开数据源不存在")

    def list_datasets(self) -> list:
        latest = {item["dataset_id"]: item for item in self.list_imports()}
        result = []
        for item in self._catalog():
            value = dict(item)
            value["last_import"] = latest.get(str(item.get("id")))
            value["ready_action"] = {
                "runtime_generator": "按上游说明在本地启动后接入",
                "gaia_bundle": "下载后选择本地文件导入",
            }.get(str(item.get("format")), "可下载官方轻量样本")
            result.append(value)
        return result

    def list_imports(self, limit: int = 100) -> list:
        with self.store.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM public_dataset_imports ORDER BY created_at DESC LIMIT ?",
                (max(1, min(int(limit), 500)),),
            ).fetchall()
        result = []
        for row in rows:
            value = dict(row)
            value["report"] = _load(value.pop("report_json", ""), {})
            result.append(value)
        return result

    def _download(self, item: Mapping[str, Any]) -> tuple[bytes, str, Path]:
        url = str(item.get("sample_url") or "").strip()
        if not url:
            raise ValueError("该数据源没有轻量下载地址，需要本地运行或手工导入")
        request = Request(url, headers={"User-Agent": "IDC-AI-Ops-Test-Lab/0.5"})
        with urlopen(request, timeout=20) as response:  # noqa: S310 - URL comes only from reviewed catalog
            length = int(response.headers.get("Content-Length") or 0)
            if length > MAX_SAMPLE_BYTES:
                raise ValueError("官方样本超过2MB，请手工下载后从本地导入")
            content = response.read(MAX_SAMPLE_BYTES + 1)
        if len(content) > MAX_SAMPLE_BYTES:
            raise ValueError("官方样本超过2MB，请手工下载后从本地导入")
        suffix = Path(url.split("?", 1)[0]).suffix or ".data"
        target = self.cache_dir / f"{item['id']}{suffix}"
        target.write_bytes(content)
        return content, url, target

    def import_sample(
        self, dataset_id: str, actor: str, sample_text: Optional[str] = None
    ) -> Dict[str, Any]:
        item = self._definition(dataset_id)
        data_format = str(item.get("format") or "")
        if data_format == "runtime_generator" and sample_text is None:
            return {
                "dataset_id": dataset_id,
                "status": "requires_runtime",
                "message": "该项目需要在本地运行后持续生成遥测；系统不会把演示数据冒充生产数据。",
                "project_url": item.get("project_url"),
            }
        if not item.get("sample_url") and sample_text is None:
            return {
                "dataset_id": dataset_id,
                "status": "requires_manual_import",
                "message": "请按上游项目说明下载数据，再从本地导入；仓库不打包受限或大体量原始数据。",
                "project_url": item.get("project_url"),
            }

        import_id = f"PDI-{uuid.uuid4().hex[:12].upper()}"
        created_at = utc_now()
        source_uri = "provided_test_content"
        local_path = ""
        if sample_text is not None:
            content = sample_text.encode("utf-8")
            suffix = ".json" if data_format == "redfish_json" else ".txt"
            target = self.cache_dir / f"{dataset_id}-{import_id}{suffix}"
            target.write_bytes(content)
            local_path = str(target)
        else:
            content, source_uri, target = self._download(item)
            local_path = str(target)
        checksum = hashlib.sha256(content).hexdigest()
        try:
            report = self._parse(item, content, import_id)
            status = "completed"
        except Exception as exc:
            report = {
                "summary": "导入失败，未把未完成结果当作有效测试数据",
                "errors": [str(exc)],
            }
            status = "failed"
        completed_at = utc_now()
        result = {
            "id": import_id,
            "dataset_id": dataset_id,
            "status": status,
            "source_uri": source_uri,
            "local_path": local_path,
            "checksum": checksum,
            "record_count": int(report.get("record_count") or 0),
            "alert_count": int(report.get("alert_count") or 0),
            "error_count": len(report.get("errors") or []),
            "report": report,
            "requested_by": actor,
            "created_at": created_at,
            "completed_at": completed_at,
        }
        with self.store.connect() as connection:
            connection.execute(
                """
                INSERT INTO public_dataset_imports (
                    id, dataset_id, status, source_uri, local_path, checksum,
                    record_count, alert_count, error_count, report_json,
                    requested_by, created_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    import_id, dataset_id, status, source_uri, local_path, checksum,
                    result["record_count"], result["alert_count"], result["error_count"],
                    _dump(report), actor, created_at, completed_at,
                ),
            )
        return result

    def _parse(
        self, item: Mapping[str, Any], content: bytes, import_id: str
    ) -> Dict[str, Any]:
        data_format = str(item.get("format") or "")
        text = content.decode("utf-8-sig", errors="replace")
        if data_format == "log_text":
            return self._parse_logs(str(item["id"]), text, import_id)
        if data_format == "gaia_bundle":
            return self._parse_gaia(text, import_id)
        if data_format == "backblaze_csv":
            return self._parse_backblaze(text, import_id)
        if data_format == "redfish_json":
            return self._parse_redfish(text, import_id)
        raise ValueError(f"当前导入器不支持格式：{data_format}")

    def _parse_logs(self, dataset_id: str, text: str, import_id: str) -> Dict[str, Any]:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        observations = []
        for index, line in enumerate(lines[:10000]):
            lowered = f" {line.lower()} "
            if not any(word in lowered for word in ANOMALY_WORDS):
                continue
            hostname_match = re.match(
                r"^[A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}\s+(\S+)", line
            )
            device_name = hostname_match.group(1) if hostname_match else f"{dataset_id}-sample"
            signal_type = self._log_signal_type(lowered)
            observations.append(
                self.governance.ingest_alert(
                    {
                        "source_system": dataset_id,
                        "source_event_id": f"{import_id}-{index}",
                        "site": "PUBLIC-LAB",
                        "entity": {
                            "device_name": device_name,
                            "device_type": "server",
                        },
                        "signal_type": signal_type,
                        "severity": "warning",
                        "summary": f"公开系统日志中发现{signal_type}线索",
                        "raw_payload": {"line": line, "line_number": index + 1},
                    }
                )
            )
            if len(observations) >= 50:
                break
        alert_ids = {result["alert"]["id"] for result in observations}
        return {
            "summary": "已按公开日志原文测试解析、去重和事故入口；结果不代表客户现场准确率。",
            "record_count": len(lines),
            "signal_observation_count": len(observations),
            "alert_count": len(alert_ids),
            "duplicate_signal_count": sum(1 for result in observations if result.get("duplicate")),
            "unique_incident_count": len(
                {result["alert"]["incident_id"] for result in observations if result["alert"]["incident_id"]}
            ),
            "errors": [],
        }

    @staticmethod
    def _log_signal_type(lowered: str) -> str:
        categories = (
            (("authentication failure", "failed password"), "authentication_failure"),
            (("i/o error", "input/output error"), "disk_io_error"),
            (("segfault", "segmentation fault"), "process_segfault"),
            (("link down", "changed state to down"), "network_link_down"),
            (("failed to start", "service failed"), "service_start_failure"),
            (("kernel panic", " panic"), "kernel_panic"),
            ((" fatal", "critical"), "fatal_or_critical"),
        )
        for needles, signal_type in categories:
            if any(needle in lowered for needle in needles):
                return signal_type
        return "public_log_anomaly"

    def _parse_gaia(self, text: str, import_id: str) -> Dict[str, Any]:
        rows = list(csv.DictReader(io.StringIO(text)))
        alerts = []
        errors = []
        for index, row in enumerate(rows[:10000]):
            label = str(row.get("label") or row.get("anomaly") or "0").strip().lower()
            if label not in {"1", "true", "anomaly", "abnormal"}:
                continue
            try:
                alerts.append(
                    self.governance.ingest_alert(
                        {
                            "source_system": "gaia_aiops",
                            "source_event_id": f"{import_id}-{index}",
                            "site": "PUBLIC-LAB",
                            "entity": {"asset_id": "gaia-metric-sample", "device_type": "metric"},
                            "signal_type": "labeled_metric_anomaly",
                            "severity": "warning",
                            "summary": "GAIA标签指标异常",
                            "raw_payload": row,
                        }
                    )
                )
            except ValueError as exc:
                errors.append(f"第{index + 2}行：{exc}")
        return {
            "summary": "已用GAIA标签测试指标异常接入；未使用标签外信息猜测根因。",
            "record_count": len(rows),
            "alert_count": len(alerts),
            "errors": errors[:50],
        }

    def _parse_backblaze(self, text: str, import_id: str) -> Dict[str, Any]:
        rows = list(csv.DictReader(io.StringIO(text)))
        headers = list(rows[0].keys()) if rows else next(csv.reader(io.StringIO(text)), [])
        alerts = []
        for index, row in enumerate(rows[:10000]):
            failed = str(row.get("failure") or row.get("failed") or "0").strip().lower()
            if failed not in {"1", "true", "yes"}:
                continue
            serial = str(row.get("serial_number") or f"backblaze-row-{index}")
            alerts.append(
                self.governance.ingest_alert(
                    {
                        "source_system": "backblaze_drive_stats",
                        "source_event_id": f"{import_id}-{index}",
                        "site": "PUBLIC-LAB",
                        "entity": {"sn": serial, "device_type": "disk"},
                        "signal_type": "disk_failure_label",
                        "severity": "critical",
                        "summary": "Backblaze数据标记磁盘失败",
                        "raw_payload": row,
                    }
                )
            )
        schema_only = "failure" not in headers and "failed" not in headers
        return {
            "summary": "已验证Backblaze字段定义；轻量下载是Schema，不把Schema伪装成磁盘故障。" if schema_only else "已导入Backblaze磁盘快照与失败标签。",
            "record_count": len(rows),
            "alert_count": len(alerts),
            "schema_only": schema_only,
            "columns": headers[:300],
            "errors": [],
        }

    def _parse_redfish(self, text: str, import_id: str) -> Dict[str, Any]:
        value = json.loads(text)
        if not isinstance(value, Mapping):
            raise ValueError("Redfish样本必须是JSON对象")
        serial = str(value.get("SerialNumber") or value.get("Id") or "redfish-sample")
        entity = f"sn:{serial}"
        for field_name, field_value in (
            ("sn", serial),
            ("device_name", value.get("Name") or value.get("Model") or value.get("Id")),
        ):
            if field_value:
                self.governance.record_identity_assertion(
                    {
                        "entity_key": entity,
                        "source_system": "bmc_redfish",
                        "field_name": field_name,
                        "field_value": str(field_value),
                    },
                    "public-dataset-import",
                )
        status = value.get("Status") if isinstance(value.get("Status"), Mapping) else {}
        health = str(status.get("Health") or status.get("HealthRollup") or "Unknown")
        state = str(status.get("State") or "Unknown")
        alerts = []
        if health.lower() not in {"ok", "unknown"} or state.lower() in {"disabled", "unavailable", "offline"}:
            alerts.append(
                self.governance.ingest_alert(
                    {
                        "source_system": "bmc_redfish",
                        "source_event_id": import_id,
                        "site": "PUBLIC-LAB",
                        "entity": {"sn": serial, "device_type": "server"},
                        "signal_type": "redfish_health",
                        "severity": "critical" if health.lower() == "critical" else "warning",
                        "summary": f"Redfish健康状态：{health}/{state}",
                        "raw_payload": dict(value),
                    }
                )
            )
        return {
            "summary": "官方Redfish样本状态健康，已验证资产字段但没有虚构故障。" if not alerts else "官方Redfish样本包含非健康状态，已进入告警治理。",
            "record_count": 1,
            "alert_count": len(alerts),
            "health": health,
            "state": state,
            "errors": [],
        }
