"""Zero-dependency HTTP API and static web server."""

from __future__ import annotations

import argparse
import json
import logging
import mimetypes
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlparse

from .auth import (
    can_decide_permission,
    can_import_work_order,
    can_operate_onsite,
    can_review_operation,
    can_manage_incident_governance,
    can_manage_maintenance,
    can_manage_trust_data,
    can_manage_drills,
    is_ai_admin,
    is_super_admin,
    normalize_role,
)
from .demo_cases import DEMO_CASES, list_demos
from .lab import PlatformUnavailable
from .service import IncidentService
from .store import IncidentStore
from .views import project_production_alert, project_public_dataset


LOG = logging.getLogger("idcops")
MAX_BODY_BYTES = 2 * 1024 * 1024


class APIError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class AppHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: Tuple[str, int],
        handler_class: Any,
        service: IncidentService,
        web_dir: Path,
    ) -> None:
        super().__init__(address, handler_class)
        self.service = service
        self.web_dir = web_dir.resolve()


class RequestHandler(BaseHTTPRequestHandler):
    server_version = "IDCAIOps/0.4"

    @property
    def app(self) -> AppHTTPServer:
        return self.server  # type: ignore[return-value]

    def log_message(self, fmt: str, *args: Any) -> None:
        LOG.info("%s - %s", self.address_string(), fmt % args)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(HTTPStatus.NO_CONTENT)
        self._common_headers()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header(
            "Access-Control-Allow-Headers", "Content-Type, X-IDCAI-Role, X-IDCAI-User"
        )
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        try:
            parsed_url = urlparse(self.path)
            path = parsed_url.path
            if path.startswith("/api/admin/"):
                self._require_admin()
            if path == "/api/admin/summary":
                self._json(HTTPStatus.OK, self.app.service.admin.summary())
            elif path == "/api/admin/records":
                query = parse_qs(parsed_url.query)
                record_type = query.get("type", ["incidents"])[0]
                search = query.get("q", [""])[0]
                limit = int(query.get("limit", ["100"])[0])
                self._json(
                    HTTPStatus.OK,
                    self.app.service.admin.list_records(record_type, search, limit),
                )
            elif path == "/api/admin/activity":
                query = parse_qs(parsed_url.query)
                limit = int(query.get("limit", ["100"])[0])
                self._json(
                    HTTPStatus.OK,
                    {"items": self.app.service.admin.list_activity(limit)},
                )
            elif path == "/api/admin/knowledge":
                self._json(
                    HTTPStatus.OK,
                    {"items": self.app.service.assets.list_knowledge()},
                )
            elif path.startswith("/api/admin/knowledge/"):
                card_id = unquote(path.removeprefix("/api/admin/knowledge/"))
                item = self.app.service.assets.get_knowledge(card_id)
                if item is None:
                    raise APIError(HTTPStatus.NOT_FOUND, "知识卡不存在")
                self._json(HTTPStatus.OK, item)
            elif path == "/api/admin/prompts":
                self._json(
                    HTTPStatus.OK,
                    {"items": self.app.service.assets.list_prompts()},
                )
            elif path.startswith("/api/admin/prompts/"):
                prompt_key = unquote(path.removeprefix("/api/admin/prompts/"))
                item = self.app.service.assets.get_prompt(prompt_key)
                if item is None:
                    raise APIError(HTTPStatus.NOT_FOUND, "提示词不存在")
                self._json(HTTPStatus.OK, item)
            elif path == "/api/admin/constraints":
                self._json(
                    HTTPStatus.OK,
                    {"items": self.app.service.constraints.list()},
                )
            elif path.startswith("/api/admin/constraints/"):
                policy_key = unquote(path.removeprefix("/api/admin/constraints/"))
                item = self.app.service.constraints.get(policy_key)
                if item is None:
                    raise APIError(HTTPStatus.NOT_FOUND, "约束策略不存在")
                self._json(HTTPStatus.OK, item)
            elif path == "/api/admin/retrieval-tests":
                self._json(
                    HTTPStatus.OK,
                    {"items": self.app.service.retrieval_tests.list()},
                )
            elif path == "/api/admin/rag-index":
                self._json(
                    HTTPStatus.OK,
                    self.app.service.retrieval_tests.index_status(),
                )
            elif path == "/api/admin/releases":
                self._json(
                    HTTPStatus.OK,
                    {"items": self.app.service.releases.list()},
                )
            elif path == "/api/admin/providers":
                self._json(HTTPStatus.OK, {"items": self.app.service.providers.list()})
            elif path.startswith("/api/admin/providers/"):
                provider_key = unquote(path.removeprefix("/api/admin/providers/"))
                item = self.app.service.providers.get(provider_key)
                if item is None:
                    raise APIError(HTTPStatus.NOT_FOUND, "模型提供方不存在")
                self._json(HTTPStatus.OK, item)
            elif path == "/api/admin/rag-runs":
                self._json(
                    HTTPStatus.OK,
                    {"items": self.app.service.rag_traces.list()},
                )
            elif path.startswith("/api/admin/rag-runs/"):
                run_id = unquote(path.removeprefix("/api/admin/rag-runs/"))
                item = self.app.service.rag_traces.get(run_id)
                if item is None:
                    raise APIError(HTTPStatus.NOT_FOUND, "RAG运行记录不存在")
                self._json(HTTPStatus.OK, item)
            elif path == "/api/admin/raw-access-audit":
                self._require_super_admin()
                self._json(
                    HTTPStatus.OK,
                    {"items": self.app.service.raw_access.list_audit()},
                )
            elif path == "/api/admin/backups":
                self._json(HTTPStatus.OK, {"items": self.app.service.backups.list()})
            elif path == "/api/health":
                source_statuses = self.app.service.source_statuses(check_external=False)
                self._json(
                    HTTPStatus.OK,
                    {
                        "ok": True,
                        "service": "IDC AI 故障调查台",
                        "ai_enabled": self.app.service.ai.enabled,
                        "analysis_mode": "ai_enriched" if self.app.service.ai.enabled else "rules_only",
                        "collectors_connected": [
                            item["id"]
                            for item in source_statuses
                            if item.get("state") == "connected"
                        ],
                        "knowledge": self.app.service.knowledge.summary(),
                    },
                )
            elif path == "/api/sources":
                query = parse_qs(parsed_url.query)
                check_external = query.get("check", ["0"])[0] in {"1", "true", "yes"}
                self._json(
                    HTTPStatus.OK,
                    {"items": self.app.service.source_statuses(check_external=check_external)},
                )
            elif path == "/api/production/overview":
                self._json(HTTPStatus.OK, self.app.service.production.overview())
            elif path == "/api/production/alerts":
                query = parse_qs(parsed_url.query)
                lifecycle_status = query.get("status", [""])[0]
                limit = int(query.get("limit", ["200"])[0])
                self._json(
                    HTTPStatus.OK,
                    {
                        "items": [
                            project_production_alert(item, self._role())
                            for item in self.app.service.production.list_alerts(
                                lifecycle_status, limit
                            )
                        ]
                    },
                )
            elif path == "/api/production/maintenance":
                self._json(
                    HTTPStatus.OK,
                    {"items": self.app.service.production.list_maintenance_windows()},
                )
            elif path == "/api/production/source-health":
                self._require_permission(can_manage_trust_data(self._role()), "当前角色不能查看采集链路配置")
                self._json(
                    HTTPStatus.OK,
                    {"items": self.app.service.production.list_source_health()},
                )
            elif path == "/api/production/identities":
                self._require_permission(can_manage_trust_data(self._role()), "当前角色不能查看身份权威数据")
                query = parse_qs(parsed_url.query)
                self._json(
                    HTTPStatus.OK,
                    {
                        "items": self.app.service.production.list_identity_assertions(
                            query.get("entity_key", [""])[0]
                        )
                    },
                )
            elif path == "/api/production/identity-conflicts":
                query = parse_qs(parsed_url.query)
                self._json(
                    HTTPStatus.OK,
                    {
                        "items": self.app.service.production.list_identity_conflicts(
                            query.get("status", [""])[0],
                            query.get("entity_key", [""])[0],
                        )
                    },
                )
            elif path == "/api/production/changes":
                query = parse_qs(parsed_url.query)
                self._json(
                    HTTPStatus.OK,
                    {
                        "items": self.app.service.production.list_changes(
                            query.get("site", [""])[0],
                            query.get("entity_key", [""])[0],
                            int(query.get("limit", ["200"])[0]),
                        )
                    },
                )
            elif path == "/api/production/rosters":
                query = parse_qs(parsed_url.query)
                active_only = query.get("active", ["0"])[0] in {"1", "true", "yes"}
                self._json(
                    HTTPStatus.OK,
                    {"items": self.app.service.production.list_rosters(active_only)},
                )
            elif path == "/api/production/assignments":
                self._json(
                    HTTPStatus.OK,
                    {"items": self.app.service.production.list_assignments()},
                )
            elif path == "/api/production/feedback":
                self._require_permission(can_manage_incident_governance(self._role()), "当前角色不能查看关联纠正记录")
                self._json(
                    HTTPStatus.OK,
                    {"items": self.app.service.production.list_feedback()},
                )
            elif path == "/api/production/metrics":
                self._require_permission(can_manage_trust_data(self._role()), "当前角色不能查看全局调查指标")
                self._json(HTTPStatus.OK, self.app.service.production.metrics())
            elif path == "/api/public-datasets":
                self._json(
                    HTTPStatus.OK,
                    {
                        "items": [
                            project_public_dataset(item, self._role())
                            for item in self.app.service.public_datasets.list_datasets()
                        ]
                    },
                )
            elif path == "/api/public-datasets/imports":
                self._require_permission(can_manage_trust_data(self._role()), "当前角色不能查看公开数据导入记录")
                self._json(
                    HTTPStatus.OK,
                    {"items": self.app.service.public_datasets.list_imports()},
                )
            elif path == "/api/operations":
                self._json(HTTPStatus.OK, {"items": self.app.service.operations.list()})
            elif path.startswith("/api/operations/"):
                operation_id = unquote(path.removeprefix("/api/operations/"))
                item = self.app.service.operations.get(operation_id)
                if item is None:
                    raise APIError(HTTPStatus.NOT_FOUND, "现场操作单不存在")
                self._json(HTTPStatus.OK, item)
            elif path == "/api/lab/platforms":
                self._require_admin()
                self._json(HTTPStatus.OK, {"items": self.app.service.lab.list_platforms()})
            elif path == "/api/lab/events":
                self._require_admin()
                query = parse_qs(parsed_url.query)
                limit = int(query.get("limit", ["200"])[0])
                self._json(
                    HTTPStatus.OK,
                    {"items": self.app.service.list_lab_events_for_default_view(limit)},
                )
            elif path == "/api/lab/topology":
                self._require_admin()
                self._json(HTTPStatus.OK, self.app.service.lab.topology())
            elif path == "/api/lab/scenarios":
                self._require_admin()
                self._json(HTTPStatus.OK, {"items": self.app.service.list_lab_scenarios()})
            elif path == "/api/drills/catalog":
                self._require_permission(can_manage_drills(self._role()), "当前角色不能查看故障演练库")
                query = parse_qs(parsed_url.query)
                self._json(
                    HTTPStatus.OK,
                    self.app.service.drills.list_catalog(query.get("category", [""])[0]),
                )
            elif path == "/api/drills/runs":
                self._require_permission(can_manage_drills(self._role()), "当前角色不能查看故障演练")
                query = parse_qs(parsed_url.query)
                self._json(
                    HTTPStatus.OK,
                    {"items": self.app.service.drills.list_runs(int(query.get("limit", ["100"])[0]))},
                )
            elif path.startswith("/api/drills/runs/"):
                self._require_permission(can_manage_drills(self._role()), "当前角色不能查看故障演练")
                run_id = unquote(path.removeprefix("/api/drills/runs/"))
                query = parse_qs(parsed_url.query)
                reveal = query.get("reveal", ["0"])[0] in {"1", "true", "yes"}
                if reveal:
                    self._require_drill_reveal(run_id)
                self._json(HTTPStatus.OK, self.app.service.drills.get(run_id, reveal=reveal))
            elif path == "/api/agent/runs":
                self._require_admin()
                query = parse_qs(parsed_url.query)
                incident_id = query.get("incident_id", [""])[0]
                self._json(
                    HTTPStatus.OK,
                    {"items": self.app.service.list_agent_runs_for_default_view(incident_id)},
                )
            elif path.startswith("/api/agent/runs/"):
                self._require_admin()
                run_id = unquote(path.removeprefix("/api/agent/runs/"))
                item = self.app.service.get_agent_run_for_default_view(run_id)
                if item is None:
                    raise APIError(HTTPStatus.NOT_FOUND, "Agent调查记录不存在")
                self._json(HTTPStatus.OK, item)
            elif path == "/api/incidents":
                incidents = self.app.service.list_incidents_for_role(self._role())
                counts = {"new": 0, "processing": 0, "resolved": 0}
                for incident in incidents:
                    counts[incident["status"]] = counts.get(incident["status"], 0) + 1
                self._json(HTTPStatus.OK, {"items": incidents, "counts": counts})
            elif path == "/api/facilities":
                self._json(
                    HTTPStatus.OK,
                    {"items": self.app.service.list_facility_profiles()},
                )
            elif path.startswith("/api/incidents/"):
                incident_id = unquote(path.removeprefix("/api/incidents/"))
                incident = self.app.service.get_incident_for_role(incident_id, self._role())
                if incident is None:
                    raise APIError(HTTPStatus.NOT_FOUND, "事件不存在")
                self._json(HTTPStatus.OK, incident)
            elif path == "/api/demos":
                self._json(HTTPStatus.OK, {"items": list_demos()})
            elif path.startswith("/api/"):
                raise APIError(HTTPStatus.NOT_FOUND, "接口不存在")
            else:
                self._static(path)
        except APIError as exc:
            self._json(exc.status, {"error": exc.message})
        except Exception as exc:  # noqa: BLE001
            LOG.exception("GET failed")
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"服务器处理失败：{exc}"})

    def do_POST(self) -> None:  # noqa: N802
        try:
            path = urlparse(self.path).path
            payload = self._read_json()
            if path.startswith("/api/admin/"):
                self._require_admin()
            parts = [unquote(item) for item in path.strip("/").split("/")]
            if path == "/api/admin/annotations":
                result = self.app.service.admin.add_annotation(payload, self._actor())
                self._json(HTTPStatus.CREATED, result)
            elif path == "/api/admin/raw-access":
                self._require_super_admin()
                result = self.app.service.raw_access.open(
                    str(payload.get("record_type") or ""),
                    str(payload.get("record_id") or ""),
                    str(payload.get("reason") or ""),
                    self._actor(),
                    self._role(),
                    bool(payload.get("confirmed")),
                )
                self._json(HTTPStatus.OK, result)
            elif path == "/api/admin/backups":
                self._json(
                    HTTPStatus.CREATED,
                    self.app.service.backups.create(self._actor()),
                )
            elif len(parts) == 5 and parts[:3] == ["api", "admin", "prompts"] and parts[4] == "versions":
                result = self.app.service.assets.create_prompt_version(
                    parts[3], payload, self._actor()
                )
                self._json(HTTPStatus.CREATED, result)
            elif len(parts) == 5 and parts[:3] == ["api", "admin", "prompts"] and parts[4] == "preview":
                result = self.app.service.assets.preview_prompt(
                    parts[3],
                    str(payload.get("version") or ""),
                    payload.get("variables", {}) if isinstance(payload.get("variables"), dict) else {},
                )
                self._json(HTTPStatus.OK, result)
            elif len(parts) == 5 and parts[:3] == ["api", "admin", "knowledge"] and parts[4] == "versions":
                result = self.app.service.assets.create_knowledge_version(
                    parts[3], payload, self._actor()
                )
                self._json(HTTPStatus.CREATED, result)
            elif len(parts) == 5 and parts[:3] == ["api", "admin", "constraints"] and parts[4] == "versions":
                result = self.app.service.constraints.create_version(
                    parts[3], payload, self._actor()
                )
                self._json(HTTPStatus.CREATED, result)
            elif path == "/api/admin/retrieval-tests":
                result = self.app.service.retrieval_tests.run(payload, self._actor())
                self._json(HTTPStatus.CREATED, result)
            elif path == "/api/admin/releases/test":
                result = self.app.service.releases.test_asset(payload, self._actor())
                self._json(HTTPStatus.CREATED, result)
            elif len(parts) == 4 and parts[:3] == ["api", "admin", "providers"]:
                result = self.app.service.providers.upsert(parts[3], payload)
                self._json(HTTPStatus.OK, result)
            elif len(parts) == 5 and parts[:3] == ["api", "admin", "releases"] and parts[4] == "prepare":
                self._json(HTTPStatus.OK, self.app.service.releases.prepare(parts[3]))
            elif len(parts) == 5 and parts[:3] == ["api", "admin", "releases"] and parts[4] == "publish":
                self._json(
                    HTTPStatus.OK,
                    self.app.service.releases.publish(
                        parts[3], bool(payload.get("confirmed_online")), self._actor()
                    ),
                )
            elif len(parts) == 5 and parts[:3] == ["api", "admin", "releases"] and parts[4] == "rollback":
                self._json(
                    HTTPStatus.OK,
                    self.app.service.releases.rollback(parts[3], self._actor()),
                )
            elif path == "/api/operations/import":
                self._require_permission(can_import_work_order(self._role()), "当前角色不能导入 OMS 工单")
                self._json(
                    HTTPStatus.CREATED,
                    self.app.service.operations.import_work_order(payload, self._actor()),
                )
            elif path == "/api/production/alerts":
                self._require_permission(can_manage_incident_governance(self._role()), "当前角色不能写入生产告警")
                result = self.app.service.production.ingest_alert(payload)
                result["alert"] = project_production_alert(result["alert"], self._role())
                self._json(
                    HTTPStatus.OK if result.get("duplicate") else HTTPStatus.CREATED,
                    result,
                )
            elif len(parts) == 5 and parts[:3] == ["api", "production", "alerts"] and parts[4] == "acknowledge":
                self._require_permission(can_manage_incident_governance(self._role()), "当前角色不能确认生产告警")
                self._json(
                    HTTPStatus.OK,
                    project_production_alert(
                        self.app.service.production.acknowledge_alert(parts[3], self._actor()),
                        self._role(),
                    ),
                )
            elif path == "/api/production/maintenance":
                self._require_permission(can_manage_maintenance(self._role()), "当前角色不能创建维护窗口")
                self._json(
                    HTTPStatus.CREATED,
                    self.app.service.production.create_maintenance_window(payload, self._actor()),
                )
            elif path == "/api/production/source-health":
                self._require_permission(can_manage_trust_data(self._role()), "当前角色不能修改采集链路状态")
                self._json(
                    HTTPStatus.OK,
                    self.app.service.production.update_source_health(payload),
                )
            elif path == "/api/production/identities":
                self._require_permission(can_manage_trust_data(self._role()), "当前角色不能写入身份权威数据")
                self._json(
                    HTTPStatus.CREATED,
                    self.app.service.production.record_identity_assertion(payload, self._actor()),
                )
            elif len(parts) == 5 and parts[:3] == ["api", "production", "identity-conflicts"] and parts[4] == "resolve":
                self._require_permission(can_manage_trust_data(self._role()), "当前角色不能处理身份冲突")
                self._json(
                    HTTPStatus.OK,
                    self.app.service.production.resolve_identity_conflict(
                        parts[3], str(payload.get("resolution") or ""), self._actor()
                    ),
                )
            elif path == "/api/production/changes":
                self._require_permission(can_manage_incident_governance(self._role()), "当前角色不能写入变更记录")
                self._json(
                    HTTPStatus.CREATED,
                    self.app.service.production.record_change(payload, self._actor()),
                )
            elif path == "/api/production/rosters":
                self._require_permission(can_manage_maintenance(self._role()), "当前角色不能维护值班表")
                self._json(
                    HTTPStatus.CREATED,
                    self.app.service.production.create_roster(payload, self._actor()),
                )
            elif path == "/api/production/assignments":
                self._require_permission(can_manage_incident_governance(self._role()), "当前角色不能分派事故")
                self._json(
                    HTTPStatus.CREATED,
                    self.app.service.production.assign_incident(payload, self._actor()),
                )
            elif len(parts) == 5 and parts[:3] == ["api", "production", "assignments"] and parts[4] == "acknowledge":
                self._require_permission(can_review_operation(self._role()), "当前角色不能确认事故分派")
                self._require_assignee_or_admin(parts[3])
                self._json(
                    HTTPStatus.OK,
                    self.app.service.production.acknowledge_assignment(parts[3], self._actor()),
                )
            elif len(parts) == 5 and parts[:3] == ["api", "production", "assignments"] and parts[4] == "defer":
                self._require_permission(can_review_operation(self._role()), "当前角色不能延后事故分派")
                self._require_assignee_or_admin(parts[3])
                self._json(
                    HTTPStatus.OK,
                    self.app.service.production.defer_assignment(
                        parts[3], str(payload.get("reason") or ""), self._actor()
                    ),
                )
            elif len(parts) == 5 and parts[:3] == ["api", "production", "assignments"] and parts[4] == "escalate":
                self._require_permission(can_manage_maintenance(self._role()), "当前角色不能升级事故")
                self._json(
                    HTTPStatus.OK,
                    self.app.service.production.escalate_assignment(
                        parts[3], str(payload.get("escalated_to") or ""), self._actor()
                    ),
                )
            elif path == "/api/production/feedback":
                self._require_permission(can_manage_incident_governance(self._role()), "当前角色不能纠正事故关联")
                self._json(
                    HTTPStatus.CREATED,
                    self.app.service.production.record_feedback(payload, self._actor()),
                )
            elif path == "/api/production/metrics":
                self._require_permission(can_manage_trust_data(self._role()), "当前角色不能写入调查指标")
                self._json(
                    HTTPStatus.CREATED,
                    self.app.service.production.record_metric(
                        str(payload.get("incident_id") or ""),
                        str(payload.get("metric_name") or ""),
                        float(payload.get("metric_value") or 0),
                        payload.get("dimensions", {}) if isinstance(payload.get("dimensions"), dict) else {},
                    ),
                )
            elif len(parts) == 4 and parts[:2] == ["api", "public-datasets"] and parts[3] == "import-sample":
                self._require_permission(can_manage_trust_data(self._role()), "当前角色不能导入公开测试数据")
                sample_text = payload.get("sample_text")
                if sample_text is not None and not isinstance(sample_text, str):
                    raise ValueError("sample_text 必须是文本")
                self._json(
                    HTTPStatus.CREATED,
                    self.app.service.public_datasets.import_sample(
                        parts[2], self._actor(), sample_text=sample_text
                    ),
                )
            elif path == "/api/lab/events":
                self._require_admin()
                result = self.app.service.ingest_platform_event_for_role(payload, self._role())
                self._json(HTTPStatus.OK if result.get("duplicate") else HTTPStatus.CREATED, result)
            elif path == "/api/agent/runs":
                self._require_admin()
                result = self.app.service.run_agent(
                    str(payload.get("incident_id") or ""),
                    str(payload.get("mode") or "baseline"),
                    int(payload.get("max_rounds") or 5),
                )
                self._json(
                    HTTPStatus.CREATED,
                    self.app.service.get_agent_run_for_default_view(result["id"]),
                )
            elif path == "/api/lab/topology/seed":
                self._require_admin()
                self._json(HTTPStatus.OK, self.app.service.lab.seed_default_topology())
            elif len(parts) == 5 and parts[:3] == ["api", "lab", "scenarios"] and parts[4] == "run":
                self._require_admin()
                result = self.app.service.run_lab_scenario_for_role(parts[3], self._role())
                self._json(HTTPStatus.CREATED, result)
            elif len(parts) == 5 and parts[:3] == ["api", "lab", "platforms"] and parts[4] == "state":
                self._require_admin()
                result = self.app.service.lab.set_platform_state(
                    parts[3],
                    str(payload.get("state") or ""),
                    int(payload.get("latency_ms") or 0),
                    str(payload.get("last_error") or ""),
                )
                self._json(HTTPStatus.OK, result)
            elif path == "/api/drills/runs":
                self._require_permission(can_manage_drills(self._role()), "当前角色不能启动故障演练")
                self._json(
                    HTTPStatus.CREATED,
                    self.app.service.drills.start(payload, self._actor(), self._role()),
                )
            elif len(parts) == 5 and parts[:3] == ["api", "drills", "runs"] and parts[4] == "advance":
                self._require_permission(can_manage_drills(self._role()), "当前角色不能推进故障演练")
                self._json(
                    HTTPStatus.OK,
                    self.app.service.drills.advance(
                        parts[3], str(payload.get("command") or "step"), self._actor()
                    ),
                )
            elif len(parts) == 5 and parts[:3] == ["api", "drills", "runs"] and parts[4] == "feedback":
                self._require_permission(can_manage_drills(self._role()), "当前角色不能提交演练反馈")
                self._json(
                    HTTPStatus.OK,
                    self.app.service.drills.feedback(
                        parts[3],
                        str(payload.get("action_id") or ""),
                        str(payload.get("notes") or ""),
                        self._actor(),
                    ),
                )
            elif len(parts) == 5 and parts[:3] == ["api", "drills", "runs"] and parts[4] == "terminate":
                self._require_permission(can_manage_drills(self._role()), "当前角色不能终止故障演练")
                self._json(
                    HTTPStatus.OK,
                    self.app.service.drills.terminate(
                        parts[3], str(payload.get("reason") or ""), self._actor()
                    ),
                )
            elif len(parts) == 4 and parts[:2] == ["api", "operations"] and parts[3] == "identity":
                self._require_permission(can_operate_onsite(self._role()), "当前角色不能执行现场身份核对")
                self._json(
                    HTTPStatus.OK,
                    self.app.service.operations.verify_identity(parts[2], payload, self._actor()),
                )
            elif len(parts) == 4 and parts[:2] == ["api", "operations"] and parts[3] == "permission":
                self._require_permission(can_decide_permission(self._role()), "当前角色不能确认操作许可")
                self._json(
                    HTTPStatus.OK,
                    self.app.service.operations.set_permission(
                        parts[2],
                        str(payload.get("decision") or ""),
                        self._actor(),
                        str(payload.get("reason") or ""),
                    ),
                )
            elif len(parts) == 4 and parts[:2] == ["api", "operations"] and parts[3] == "review":
                self._require_permission(can_review_operation(self._role()), "当前角色不能复核现场操作")
                self._json(
                    HTTPStatus.OK,
                    self.app.service.operations.review(parts[2], payload, self._actor()),
                )
            elif len(parts) == 4 and parts[:2] == ["api", "operations"] and parts[3] == "start":
                self._require_permission(can_operate_onsite(self._role()), "当前角色不能开始现场操作")
                self._json(
                    HTTPStatus.OK,
                    self.app.service.operations.start(parts[2], self._actor()),
                )
            elif len(parts) == 4 and parts[:2] == ["api", "operations"] and parts[3] == "complete":
                self._require_permission(can_operate_onsite(self._role()), "当前角色不能结束现场操作")
                self._json(
                    HTTPStatus.OK,
                    self.app.service.operations.complete(parts[2], payload, self._actor()),
                )
            elif path == "/api/ingest/alert":
                incident = self.app.service.ingest("monitor", payload)
                self._json(HTTPStatus.CREATED, incident)
            elif path == "/api/ingest/signoz-alert":
                incidents = self.app.service.ingest_signoz_alert(payload)
                self._json(HTTPStatus.CREATED, {"incidents": incidents})
            elif path == "/api/ingest/log":
                incident = self.app.service.ingest("log", payload)
                self._json(HTTPStatus.CREATED, incident)
            elif path == "/api/ingest/onsite":
                incident = self.app.service.ingest("onsite", payload)
                self._json(HTTPStatus.CREATED, incident)
            elif path.startswith("/api/incidents/") and path.endswith("/status"):
                incident_id = unquote(path.removeprefix("/api/incidents/").removesuffix("/status"))
                incident = self.app.service.update_status(incident_id, str(payload.get("status", "")))
                if incident is None:
                    raise APIError(HTTPStatus.NOT_FOUND, "事件不存在")
                self._json(HTTPStatus.OK, incident)
            elif path.startswith("/api/incidents/") and path.endswith("/investigate"):
                incident_id = unquote(
                    path.removeprefix("/api/incidents/").removesuffix("/investigate")
                )
                incident = self.app.service.investigate_external(incident_id)
                if incident is None:
                    raise APIError(HTTPStatus.NOT_FOUND, "事件不存在")
                self._json(HTTPStatus.OK, incident)
            elif path == "/api/sources/check":
                self._json(
                    HTTPStatus.OK,
                    {"items": self.app.service.source_statuses(check_external=True)},
                )
            elif path == "/api/facilities":
                profile = self.app.service.upsert_facility_profile(payload)
                self._json(HTTPStatus.OK, profile)
            elif path.startswith("/api/demos/") and path.endswith("/run"):
                demo_id = unquote(path.removeprefix("/api/demos/").removesuffix("/run"))
                case = DEMO_CASES.get(demo_id)
                if case is None:
                    raise APIError(HTTPStatus.NOT_FOUND, "演练场景不存在")
                results = []
                for item in case["inputs"]:
                    demo_payload = dict(item)
                    source = str(demo_payload.pop("source"))
                    demo_payload["demo_id"] = demo_id
                    demo_payload["is_demo"] = True
                    results.append(self.app.service.ingest(source, demo_payload))
                unique = {item["id"]: item for item in results}
                self._json(
                    HTTPStatus.CREATED,
                    {"demo": demo_id, "incidents": list(unique.values())},
                )
            else:
                raise APIError(HTTPStatus.NOT_FOUND, "接口不存在")
        except APIError as exc:
            self._json(exc.status, {"error": exc.message})
        except PlatformUnavailable as exc:
            self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": str(exc)})
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            LOG.exception("POST failed")
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"服务器处理失败：{exc}"})

    def _read_json(self) -> Dict[str, Any]:
        content_length = self.headers.get("Content-Length")
        if content_length is None:
            raise APIError(HTTPStatus.LENGTH_REQUIRED, "缺少 Content-Length")
        try:
            length = int(content_length)
        except ValueError as exc:
            raise APIError(HTTPStatus.BAD_REQUEST, "Content-Length 无效") from exc
        if length <= 0:
            return {}
        if length > MAX_BODY_BYTES:
            raise APIError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "输入超过 2MB 限制")
        body = self.rfile.read(length)
        try:
            value = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise APIError(HTTPStatus.BAD_REQUEST, "请求必须是有效 UTF-8 JSON") from exc
        if not isinstance(value, dict):
            raise APIError(HTTPStatus.BAD_REQUEST, "请求 JSON 必须是对象")
        return value

    def _static(self, path: str) -> None:
        relative = "index.html" if path in {"", "/"} else unquote(path).lstrip("/")
        target = (self.app.web_dir / relative).resolve()
        if self.app.web_dir not in target.parents and target != self.app.web_dir:
            raise APIError(HTTPStatus.FORBIDDEN, "禁止访问该路径")
        if not target.is_file():
            target = self.app.web_dir / "index.html"
        if not target.is_file():
            raise APIError(HTTPStatus.NOT_FOUND, "页面资源不存在")
        content = target.read_bytes()
        content_type, _encoding = mimetypes.guess_type(str(target))
        self.send_response(HTTPStatus.OK)
        self._common_headers()
        self.send_header("Content-Type", (content_type or "application/octet-stream") + "; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _json(self, status: int, payload: Any) -> None:
        content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._common_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _common_headers(self) -> None:
        if os.getenv("IDCAI_ALLOW_CORS", "0") == "1":
            self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")

    def _role(self) -> str:
        return normalize_role(
            self.headers.get("X-IDCAI-Role", os.getenv("IDCAI_DEFAULT_ROLE", "ai_admin"))
        )

    def _actor(self) -> str:
        return str(self.headers.get("X-IDCAI-User", "local-admin")).strip() or "local-admin"

    def _require_admin(self) -> None:
        if not is_ai_admin(self._role()):
            raise APIError(HTTPStatus.FORBIDDEN, "当前角色没有 AI 资产管理权限")

    def _require_super_admin(self) -> None:
        if not is_super_admin(self._role()):
            raise APIError(HTTPStatus.FORBIDDEN, "仅最高管理员可突破性查看原始记录")

    def _require_assignee_or_admin(self, assignment_id: str) -> None:
        if is_ai_admin(self._role()):
            return
        assignment = next(
            (
                item
                for item in self.app.service.production.list_assignments()
                if item.get("id") == assignment_id
            ),
            None,
        )
        if assignment is None:
            raise APIError(HTTPStatus.NOT_FOUND, "事故分派不存在")
        if assignment.get("assignee") != self._actor():
            raise APIError(HTTPStatus.FORBIDDEN, "只有被分派人本人可以确认或延后；管理员代操作会单独审计")

    def _require_drill_reveal(self, run_id: str) -> None:
        run = self.app.service.drills.get(run_id, reveal=False)
        if is_super_admin(self._role()):
            return
        if run.get("started_by") != self._actor():
            raise APIError(HTTPStatus.FORBIDDEN, "只有本次演练发起人或最高管理员可在结束后揭示答案")

    @staticmethod
    def _require_permission(allowed: bool, message: str) -> None:
        if not allowed:
            raise APIError(HTTPStatus.FORBIDDEN, message)


def create_server(
    host: str = "127.0.0.1",
    port: int = 8765,
    database: Optional[str] = None,
    web_dir: Optional[str] = None,
) -> AppHTTPServer:
    if host not in {"127.0.0.1", "localhost", "::1"} and os.getenv("IDCAI_ALLOW_LAN", "0") != "1":
        raise ValueError("非本机监听需要显式设置 IDCAI_ALLOW_LAN=1")
    root = Path(__file__).resolve().parent.parent
    database_path = database or os.getenv("IDCAI_DATABASE", str(root / "data" / "incidents.db"))
    static_path = Path(web_dir) if web_dir else root / "web"
    service = IncidentService(IncidentStore(database_path))
    return AppHTTPServer((host, port), RequestHandler, service, static_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="IDC AI 故障调查与现场协同工作台")
    parser.add_argument("--host", default=os.getenv("IDCAI_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("IDCAI_PORT", "8765")))
    parser.add_argument("--database", default=os.getenv("IDCAI_DATABASE", ""))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    server = create_server(args.host, args.port, args.database or None)
    address, port = server.server_address[:2]
    print(f"IDC AI 故障调查台已启动：http://{address}:{port}")
    print("按 Ctrl+C 停止")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n正在停止…")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
