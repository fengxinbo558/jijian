"""Application service: normalize, analyze, correlate, and persist incidents."""

from __future__ import annotations

import copy
import hashlib
import uuid
from typing import Any, Dict, Iterable, List, Mapping, Optional

from .ai import AIEnricher
from .agent import AgentInvestigator
from .agent_tools import AgentToolRegistry
from .agent_trace import AgentTraceRecorder
from .assets import AssetRegistry
from .backups import BackupService
from .admin import AdminService
from .facility import assess_facility_event, strongest_assessment
from .investigation import apply_model_enrichment, build_investigation, merge_investigations
from .integrations import IntegrationHub
from .knowledge import KnowledgeBase
from .lab import IntegrationLab
from .lab_scenarios import list_scenarios, scenario_events
from .models import NormalizedInput, RuleAnalysis, utc_now
from .operations import OperationService
from .providers import ProviderRegistry
from .rules import analyze_rules
from .releases import ReleaseManager
from .rag_trace import RagTraceRecorder
from .raw_access import RawAccessService
from .store import IncidentStore
from .views import project_agent_run, project_incident, project_integration_event


SEVERITY_RANK = {"unknown": 0, "info": 1, "warning": 2, "critical": 3}


def _coalesce(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return ""


def _device_from(payload: Mapping[str, Any]) -> Dict[str, Any]:
    device = payload.get("device") if isinstance(payload.get("device"), Mapping) else {}
    return {
        "sn": _coalesce(device.get("sn"), payload.get("sn")),
        "name": _coalesce(device.get("name"), payload.get("device_name"), payload.get("hostname")),
        "rack_position": _coalesce(
            device.get("rack_position"), payload.get("rack_position"), payload.get("rack")
        ),
        "device_type": _coalesce(device.get("device_type"), payload.get("device_type"), "unknown"),
        "ip": _coalesce(device.get("ip"), payload.get("ip"), payload.get("ilo_ip")),
    }


def _operation_from(payload: Mapping[str, Any]) -> Dict[str, Any]:
    context = (
        payload.get("operation_context")
        if isinstance(payload.get("operation_context"), Mapping)
        else {}
    )
    return {
        "from_reinstall": _coalesce(
            context.get("from_reinstall"), payload.get("from_reinstall"), "unknown"
        ),
        "uid_status": _coalesce(context.get("uid_status"), payload.get("uid_status"), "unknown"),
        "power_permission": _coalesce(
            context.get("power_permission"), payload.get("power_permission"), "unknown"
        ),
        "interface_person": _coalesce(
            context.get("interface_person"), payload.get("interface_person")
        ),
        "interface_team": _coalesce(context.get("interface_team"), payload.get("interface_team")),
    }


def normalize_input(source: str, payload: Mapping[str, Any]) -> NormalizedInput:
    """Convert endpoint-specific payloads to one strict input contract."""

    labels = dict(payload.get("labels", {})) if isinstance(payload.get("labels"), Mapping) else {}
    if "cc_required" in payload:
        labels["cc_required"] = payload["cc_required"]
    if "incident_key" in payload:
        labels["incident_key"] = payload["incident_key"]
    if "demo_id" in payload:
        labels["demo_id"] = payload["demo_id"]
    if "is_demo" in payload:
        labels["is_demo"] = payload["is_demo"]
    if "source_system" in payload:
        labels["source_system"] = payload["source_system"]
    if "external_query" in payload:
        labels["external_query"] = payload["external_query"]
    for field in (
        "facility_criticality",
        "facility_criticality_source",
        "facility_name",
        "asset_criticality",
        "event_category",
        "event_subtype",
        "impact_level",
        "sop_threshold_met",
        "affected_scope",
    ):
        if field in payload:
            labels[field] = payload[field]

    if source == "monitor":
        summary = _coalesce(payload.get("summary"), payload.get("title"), payload.get("message"))
        raw_text = _coalesce(payload.get("raw_text"), payload.get("details"), payload.get("message"), summary)
    elif source == "log":
        raw_text = _coalesce(payload.get("raw_text"), payload.get("log_text"), payload.get("content"))
        summary = _coalesce(payload.get("summary"), "日志中发现异常" if raw_text else "")
    elif source == "onsite":
        raw_text = _coalesce(payload.get("raw_text"), payload.get("observation"), payload.get("description"))
        summary = _coalesce(payload.get("summary"), "现场发现异常" if raw_text else "")
    else:
        raise ValueError("unsupported source")

    return NormalizedInput.from_mapping(
        {
            "source": source,
            "event_time": payload.get("event_time"),
            "site": _coalesce(payload.get("site"), payload.get("site_code")),
            "severity": payload.get("severity"),
            "device": _device_from(payload),
            "summary": summary,
            "raw_text": raw_text,
            "labels": labels,
            "operation_context": _operation_from(payload),
        }
    )


class IncidentService:
    def __init__(
        self,
        store: IncidentStore,
        ai: Optional[AIEnricher] = None,
        knowledge: Optional[KnowledgeBase] = None,
        integrations: Optional[IntegrationHub] = None,
    ) -> None:
        self.store = store
        self.assets = AssetRegistry(store)
        self.assets.ensure_seeded()
        self.admin = AdminService(store, self.assets)
        self.releases = ReleaseManager(store, self.assets)
        self.rag_traces = RagTraceRecorder(store, self.assets)
        self.operations = OperationService(store)
        self.lab = IntegrationLab(store)
        self.agent_traces = AgentTraceRecorder(store)
        self.agent_tools = AgentToolRegistry(self.lab)
        self.providers = ProviderRegistry(store)
        self.providers.ensure_seeded()
        self.ai = ai or AIEnricher(self.assets)
        self.agent = AgentInvestigator(
            store, self.agent_tools, self.agent_traces, self.ai
        )
        self.raw_access = RawAccessService(store, self)
        self.backups = BackupService(store)
        self.knowledge = knowledge or KnowledgeBase(registry=self.assets)
        self.integrations = integrations or IntegrationHub()

    def ingest_platform_event(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        """Accept one simulated platform event through the production-shaped boundary."""

        prepared = self.lab.prepare_event(payload)
        if prepared["duplicate"]:
            event = prepared["event"]
            incident = self.get_incident(event.get("incident_id", "")) if event.get("incident_id") else None
            return {"accepted": bool(incident), "duplicate": True, "event": event, "incident": incident}
        event = prepared["event"]
        normalized = prepared["normalized"]
        try:
            incident = self.ingest(normalized["ingest_source"], normalized["ingest_payload"])
        except Exception as exc:
            self.lab.fail_event(event["id"], str(exc))
            raise
        correlation = dict(prepared.get("correlation") or {})
        completed = self.lab.complete_event(
            event["id"],
            incident["id"],
            str(incident.get("correlation_key", "")),
            correlation,
        )
        return {"accepted": True, "duplicate": False, "event": completed, "incident": incident}

    def list_lab_scenarios(self) -> list:
        return list_scenarios()

    def list_lab_events_for_default_view(self, limit: int = 200) -> list:
        return [project_integration_event(item) for item in self.lab.list_events(limit)]

    def ingest_platform_event_for_role(
        self, payload: Mapping[str, Any], role: str
    ) -> Dict[str, Any]:
        result = self.ingest_platform_event(payload)
        value = dict(result)
        if value.get("event"):
            value["event"] = project_integration_event(value["event"])
        if value.get("incident"):
            value["incident"] = project_incident(value["incident"], role)
        return value

    def run_lab_scenario(self, scenario_id: str) -> Dict[str, Any]:
        results = [self.ingest_platform_event(item) for item in scenario_events(scenario_id)]
        incident_ids = []
        for result in results:
            incident = result.get("incident") or {}
            incident_id = str(incident.get("id") or "")
            if incident_id and incident_id not in incident_ids:
                incident_ids.append(incident_id)
        return {
            "scenario_id": scenario_id,
            "deliveries": results,
            "incident_ids": incident_ids,
            "incidents": [self.get_incident(item) for item in incident_ids],
        }

    def run_lab_scenario_for_role(self, scenario_id: str, role: str) -> Dict[str, Any]:
        result = self.run_lab_scenario(scenario_id)
        return {
            "scenario_id": result["scenario_id"],
            "incident_ids": result["incident_ids"],
            "deliveries": [
                {
                    "accepted": item.get("accepted"),
                    "duplicate": item.get("duplicate"),
                    "event": project_integration_event(item.get("event") or {}),
                }
                for item in result["deliveries"]
            ],
            "incidents": [
                project_incident(item, role) for item in result["incidents"] if item
            ],
        }

    def run_agent(
        self, incident_id: str, mode: str = "baseline", max_rounds: int = 5
    ) -> Dict[str, Any]:
        incident = self.get_incident(incident_id)
        if incident is None:
            raise ValueError("事件不存在")
        return self.agent.run(incident, mode, max_rounds)

    def ingest(self, source: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
        event = normalize_input(source, payload)
        analysis = analyze_rules(event)
        facility_assessment = assess_facility_event(
            event,
            analysis,
            self.store.get_facility_profile(event.site) if event.site else None,
        )
        if facility_assessment["decision"] == "required":
            analysis.cc_required = True
            analysis.cc_reason = facility_assessment["reason"]
        correlation_key = self._correlation_key(event, analysis.category)
        investigation = build_investigation(
            event,
            analysis,
            correlation_key,
            self.knowledge,
            model_enriched=False,
        )
        enriched = self.ai.enrich(event, analysis, investigation)
        analysis_dict = self._combine_analysis(analysis, enriched)
        analysis_dict["facility_assessment"] = facility_assessment
        if enriched:
            investigation = apply_model_enrichment(investigation, enriched)
        existing = self.store.find_merge_candidate(event, analysis.category, correlation_key)
        if existing:
            investigation = merge_investigations(existing.get("investigation", {}), investigation)
            update = self._merge(existing, event, analysis, analysis_dict, investigation)
            saved = self.store.merge_incident(existing["id"], update, event)
            saved["latest_rag_run_id"] = self.rag_traces.record(saved["id"], investigation)
            return saved
        incident = self._new_incident(
            event, analysis, analysis_dict, correlation_key, investigation
        )
        saved = self.store.create_incident(incident, event)
        saved["latest_rag_run_id"] = self.rag_traces.record(saved["id"], investigation)
        return saved

    @staticmethod
    def _combine_analysis(
        analysis: RuleAnalysis, enriched: Optional[Mapping[str, Any]]
    ) -> Dict[str, Any]:
        value = analysis.to_dict()
        value["ai_mode"] = "rule"
        if enriched:
            for key in (
                "impact_summary",
                "candidate_causes",
                "suggestions",
                "missing_information",
            ):
                if key in enriched:
                    value[key] = copy.deepcopy(enriched[key])
            value["ai_mode"] = "model_enhanced"
        return value

    @staticmethod
    def _correlation_key(event: NormalizedInput, category: str) -> str:
        explicit = str(event.labels.get("incident_key", "")).strip()
        if explicit:
            return "explicit:" + explicit
        identity = event.device.identity_key() or "unidentified"
        material = f"{event.site}|{identity}|{category}".encode("utf-8")
        return "auto:" + hashlib.sha256(material).hexdigest()[:20]

    @staticmethod
    def _identity_keys(devices: Iterable[Mapping[str, Any]]) -> str:
        keys: List[str] = []
        for device in devices:
            identity = str(
                device.get("sn")
                or device.get("name")
                or device.get("ip")
                or device.get("rack_position")
                or ""
            )
            if identity and identity not in keys:
                keys.append(identity)
        return "".join(f"|{key}|" for key in keys)

    @staticmethod
    def _unique_devices(devices: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
        result: List[Dict[str, Any]] = []
        seen = set()
        for device in devices:
            value = dict(device)
            identity = (
                value.get("sn"),
                value.get("name"),
                value.get("ip"),
                value.get("rack_position"),
            )
            if identity in seen or not any(identity):
                continue
            seen.add(identity)
            result.append(value)
        return result

    @staticmethod
    def _unique_evidence(evidence: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
        result: List[Dict[str, Any]] = []
        seen = set()
        for item in evidence:
            text = str(item.get("text", ""))
            if not text or text in seen:
                continue
            seen.add(text)
            result.append(
                {"id": f"E{len(result) + 1}", "source": item.get("source", "unknown"), "text": text}
            )
        return result[:30]

    @staticmethod
    def _power_gate(event: NormalizedInput, analysis: RuleAnalysis) -> Dict[str, Any]:
        context = event.operation_context
        permission = context.power_permission.lower()
        if context.from_reinstall.lower() in {"yes", "是", "true", "1"} and permission == "unknown":
            permission = "allowed"
        if context.from_reinstall.lower() in {"no", "否", "false", "0"} and permission == "unknown":
            permission = "confirm"

        missing_identity = analysis.requires_onsite and event.device.device_type in {
            "server",
            "switch",
            "unknown",
        } and (not event.device.sn or not event.device.rack_position)
        if missing_identity:
            gate = "stop"
            message = "设备身份或位置不完整，停止现场操作并联系接口人确认"
        elif permission == "forbidden":
            gate = "stop"
            message = "当前禁止断电或影响供电的操作"
        elif permission == "allowed":
            gate = "ready"
            message = "已提供操作许可；仍需现场核对完整 SN、机架位和操作对象"
        else:
            gate = "confirm"
            message = "操作或断电许可未确认，联系接口人后再操作"
        return {"value": permission, "gate": gate, "message": message}

    def _onsite_card(self, event: NormalizedInput, analysis: RuleAnalysis) -> Dict[str, Any]:
        power = self._power_gate(event, analysis)
        return {
            "required": analysis.requires_onsite,
            "site": event.site,
            "device": event.device.to_dict(),
            "uid_status": event.operation_context.uid_status,
            "from_reinstall": event.operation_context.from_reinstall,
            "power": power,
            "interface_person": event.operation_context.interface_person,
            "interface_team": event.operation_context.interface_team,
            "identity_complete": bool(event.device.sn and event.device.rack_position),
            "missing_information": list(analysis.missing_information),
            "actions": list(analysis.suggestions),
            "stop_condition": "身份、位置、业务状态或许可不一致时立即停止并联系接口人",
        }

    @staticmethod
    def _cc_reminder(analysis: RuleAnalysis, existing: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
        if existing and existing.get("required"):
            return dict(existing)
        if not analysis.cc_required:
            return {"required": False}
        return {
            "required": True,
            "message": "请立即按现有 CC 流程拨打电话",
            "reason": analysis.cc_reason,
            "triggered_at": utc_now(),
        }

    @staticmethod
    def _communication(
        event: NormalizedInput, analysis: RuleAnalysis, devices: List[Mapping[str, Any]]
    ) -> str:
        identities = []
        for device in devices:
            parts = [
                str(device.get("sn") or "SN未知"),
                str(device.get("rack_position") or "机架位未知"),
                str(device.get("name") or "设备名未知"),
            ]
            identities.append(" / ".join(parts))
        identity_text = "；".join(identities) or "设备身份待补充"
        return (
            f"[{event.site or '机房待确认'}] {analysis.title}。"
            f"设备：{identity_text}。现象：{event.summary}。"
            f"当前判断：{analysis.candidate_causes[0]['title']}（需结合证据继续确认）。"
        )

    def _new_incident(
        self,
        event: NormalizedInput,
        analysis: RuleAnalysis,
        analysis_dict: Dict[str, Any],
        correlation_key: str,
        investigation: Dict[str, Any],
    ) -> Dict[str, Any]:
        now = utc_now()
        devices = self._unique_devices([event.device.to_dict()])
        incident_id = "INC-" + now[:10].replace("-", "") + "-" + uuid.uuid4().hex[:6].upper()
        return {
            "id": incident_id,
            "title": analysis.title,
            "status": "new",
            "severity": analysis.severity,
            "category": analysis.category,
            "site": event.site,
            "summary": event.summary,
            "correlation_key": correlation_key,
            "identity_keys": self._identity_keys(devices),
            "devices": devices,
            "evidence": self._unique_evidence(analysis.evidence),
            "analysis": analysis_dict,
            "investigation": investigation,
            "onsite_card": self._onsite_card(event, analysis),
            "cc_reminder": self._cc_reminder(analysis),
            "communication_text": self._communication(event, analysis, devices),
            "created_at": now,
            "updated_at": now,
        }

    def _merge(
        self,
        existing: Mapping[str, Any],
        event: NormalizedInput,
        analysis: RuleAnalysis,
        analysis_dict: Dict[str, Any],
        investigation: Dict[str, Any],
    ) -> Dict[str, Any]:
        devices = self._unique_devices(list(existing.get("devices", [])) + [event.device.to_dict()])
        evidence = self._unique_evidence(list(existing.get("evidence", [])) + analysis.evidence)
        severity = max(
            (str(existing.get("severity", "unknown")), analysis.severity),
            key=lambda item: SEVERITY_RANK.get(item, 0),
        )
        analysis_dict["evidence"] = evidence
        analysis_dict["facility_assessment"] = strongest_assessment(
            existing.get("analysis", {}).get("facility_assessment"),
            analysis_dict.get("facility_assessment", {}),
        )
        onsite_card = self._onsite_card(event, analysis)
        if not analysis.requires_onsite and existing.get("onsite_card", {}).get("required"):
            onsite_card = dict(existing["onsite_card"])
        return {
            "title": existing.get("title") or analysis.title,
            "severity": severity,
            "summary": existing.get("summary") or event.summary,
            "identity_keys": self._identity_keys(devices),
            "devices": devices,
            "evidence": evidence,
            "analysis": analysis_dict,
            "investigation": investigation,
            "onsite_card": onsite_card,
            "cc_reminder": self._cc_reminder(analysis, existing.get("cc_reminder")),
            "communication_text": self._communication(event, analysis, devices),
            "updated_at": utc_now(),
        }

    def list_incidents(self) -> List[Dict[str, Any]]:
        return self.store.list_incidents()

    def list_incidents_for_role(self, role: str) -> List[Dict[str, Any]]:
        return [project_incident(item, role) for item in self.store.list_incidents()]

    def get_incident(self, incident_id: str) -> Optional[Dict[str, Any]]:
        return self.store.get_incident(incident_id)

    def get_incident_for_role(self, incident_id: str, role: str) -> Optional[Dict[str, Any]]:
        incident = self.get_incident(incident_id)
        return project_incident(incident, role) if incident is not None else None

    def get_agent_run_for_default_view(self, run_id: str) -> Optional[Dict[str, Any]]:
        run = self.agent_traces.get(run_id)
        return project_agent_run(run) if run is not None else None

    def list_agent_runs_for_default_view(self, incident_id: str = "") -> list:
        return [project_agent_run(item) for item in self.agent_traces.list(incident_id)]

    def update_status(self, incident_id: str, status: str) -> Optional[Dict[str, Any]]:
        return self.store.update_status(incident_id, status)

    def list_facility_profiles(self) -> List[Dict[str, Any]]:
        return self.store.list_facility_profiles()

    def upsert_facility_profile(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        site = str(payload.get("site") or "").strip().upper()
        if not site:
            raise ValueError("机房编码不能为空")
        criticality = str(payload.get("criticality") or "unknown").strip().lower()
        if criticality not in {"core", "normal", "unknown"}:
            raise ValueError("机房等级必须是 core、normal 或 unknown")
        return self.store.upsert_facility_profile(
            {
                "site": site,
                "display_name": str(payload.get("display_name") or site).strip(),
                "criticality": criticality,
                "source": str(payload.get("source") or "local_config").strip(),
                "source_reference": str(payload.get("source_reference") or "").strip(),
                "effective_at": str(payload.get("effective_at") or utc_now()).strip(),
            }
        )

    def source_statuses(self, check_external: bool = True) -> List[Dict[str, Any]]:
        return self.integrations.source_statuses(check_external=check_external)

    def ingest_signoz_alert(self, payload: Mapping[str, Any]) -> List[Dict[str, Any]]:
        """Accept Alertmanager-style SigNoz webhooks without requiring one exact template."""

        raw_alerts = payload.get("alerts")
        alerts = list(raw_alerts) if isinstance(raw_alerts, list) else [payload]
        results: List[Dict[str, Any]] = []
        for raw in alerts[:100]:
            if not isinstance(raw, Mapping):
                continue
            labels = raw.get("labels") if isinstance(raw.get("labels"), Mapping) else {}
            annotations = (
                raw.get("annotations") if isinstance(raw.get("annotations"), Mapping) else {}
            )
            summary = _coalesce(
                annotations.get("summary"),
                annotations.get("title"),
                labels.get("alertname"),
                raw.get("title"),
                "SigNoz 监控发现异常",
            )
            details = _coalesce(
                annotations.get("description"),
                annotations.get("message"),
                raw.get("message"),
                summary,
            )
            status = str(raw.get("status") or payload.get("status") or "").lower()
            severity = str(labels.get("severity") or raw.get("severity") or "unknown").lower()
            if status == "firing" and severity == "unknown":
                severity = "warning"
            normalized = {
                "site": _coalesce(labels.get("site"), labels.get("site_code"), raw.get("site")),
                "severity": severity,
                "sn": _coalesce(
                    labels.get("serial_number"), labels.get("sn"), raw.get("sn")
                ),
                "device_name": _coalesce(
                    labels.get("host_name"),
                    labels.get("hostname"),
                    labels.get("host.name"),
                    raw.get("device_name"),
                ),
                "rack_position": _coalesce(
                    labels.get("rack_position"), labels.get("rack"), raw.get("rack_position")
                ),
                "ip": _coalesce(labels.get("instance"), labels.get("ip"), raw.get("ip")),
                "device_type": _coalesce(
                    labels.get("device_type"), raw.get("device_type"), "unknown"
                ),
                "summary": summary,
                "message": details,
                "event_time": _coalesce(raw.get("startsAt"), raw.get("event_time")),
                "incident_key": _coalesce(
                    raw.get("fingerprint"), payload.get("groupKey"), raw.get("incident_key")
                ),
                "source_system": "signoz",
                "labels": {**dict(labels), "signoz_status": status},
            }
            results.append(self.ingest("monitor", normalized))
        if not results:
            raise ValueError("SigNoz 告警中没有可处理的 alerts")
        return results

    def investigate_external(self, incident_id: str) -> Optional[Dict[str, Any]]:
        incident = self.get_incident(incident_id)
        if incident is None:
            return None
        observations = self.integrations.investigate(incident)
        investigation = copy.deepcopy(incident.get("investigation", {}))
        signoz = observations[0]
        if signoz.get("state") == "completed" and signoz.get("records"):
            device = (incident.get("devices") or [{}])[0]
            text = "\n".join(
                str(item.get("text", ""))
                for item in signoz.get("records", [])
                if isinstance(item, Mapping) and item.get("text")
            )
            if text:
                event = normalize_input(
                    "log",
                    {
                        "site": incident.get("site"),
                        "severity": incident.get("severity"),
                        "device": device,
                        "summary": "SigNoz 自动查询返回的事故时间窗日志",
                        "raw_text": text,
                        "event_time": incident.get("updated_at"),
                        "source_system": "signoz",
                        "external_query": True,
                    },
                )
                analysis = analyze_rules(event)
                child = build_investigation(
                    event,
                    analysis,
                    str(incident.get("correlation_key", "")),
                    self.knowledge,
                    model_enriched=False,
                )
                investigation = merge_investigations(investigation, child)

        investigation["external_checks"] = observations
        trace = list(investigation.get("trace", []))
        trace = [
            item
            for item in trace
            if item.get("stage") not in {"external_telemetry", "holmes_investigation"}
        ]
        trace.extend(
            [
                {
                    "stage": "external_telemetry",
                    "title": "查询真实监控数据",
                    "summary": signoz.get("message", "没有执行 SigNoz 查询"),
                    "state": "confirmed"
                    if signoz.get("state") == "completed"
                    else "waiting",
                    "limitation": "服务可连接不等于数据完整；查询只覆盖当前设备身份和事故时间窗。",
                },
                {
                    "stage": "holmes_investigation",
                    "title": "AI 调用只读工具补充调查",
                    "summary": observations[1].get("message", "没有执行 AI 工具调查"),
                    "state": "inferred"
                    if observations[1].get("state") == "completed"
                    else "waiting",
                    "limitation": "AI 回答不能覆盖设备身份、操作许可或未经工具验证的事实。",
                },
            ]
        )
        investigation["trace"] = trace
        investigation["mode"] = (
            "tool_assisted"
            if any(item.get("state") == "completed" for item in observations)
            else investigation.get("mode", "rules_only")
        )
        investigation["capability_notice"] = "；".join(
            str(item.get("message", "")) for item in observations if item.get("message")
        )
        saved = self.store.update_investigation(
            incident_id,
            investigation,
            {
                "providers": [item.get("provider") for item in observations],
                "states": [item.get("state") for item in observations],
            },
        )
        if saved is not None:
            saved["latest_rag_run_id"] = self.rag_traces.record(incident_id, investigation)
        return saved
