"""Role-specific response projections and default structured redaction."""

from __future__ import annotations

import copy
from typing import Any, Dict, Mapping

from .auth import normalize_role
from .security import redact_text


def _redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)[0]
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _redact_value(item) for key, item in value.items()}
    return value


def _summary_intake(items: list, include_redacted_excerpt: bool) -> list:
    result = []
    for item in items:
        projected = {
            key: copy.deepcopy(item.get(key))
            for key in (
                "id",
                "source",
                "source_label",
                "source_kind",
                "simulation",
                "event_time",
                "received_at",
                "summary",
                "provided_device_fields",
            )
            if key in item
        }
        projected["raw_available"] = bool(item.get("raw_text"))
        if include_redacted_excerpt and item.get("raw_text"):
            projected["redacted_excerpt"] = redact_text(str(item["raw_text"]))[0][:1200]
        result.append(projected)
    return result


def project_incident(incident: Mapping[str, Any], role: str) -> Dict[str, Any]:
    """Return only the fields required by one role's work."""

    normalized_role = normalize_role(role)
    value = _redact_value(copy.deepcopy(dict(incident)))
    value["access_scope"] = {
        "role": normalized_role,
        "default_view": "structured_redacted",
        "raw_requires_break_glass": True,
    }
    if "inputs" in value:
        value["inputs"] = [
            {
                "id": item.get("id"),
                "source": item.get("source"),
                "event_time": item.get("event_time"),
                "created_at": item.get("created_at"),
                "raw_available": bool(item.get("payload")),
            }
            for item in value.get("inputs", [])
        ]

    investigation = value.get("investigation")
    if isinstance(investigation, dict):
        include_excerpt = normalized_role in {"interface_person", "ai_admin", "super_admin"}
        investigation["intake"] = _summary_intake(
            list(investigation.get("intake", [])), include_excerpt
        )
        if normalized_role == "onsite_operator":
            allowed = {
                "schema_version",
                "mode",
                "capability_notice",
                "simulation",
                "intake",
                "field_provenance",
                "hypotheses",
                "conclusion",
                "missing_information",
                "next_steps",
            }
            value["investigation"] = {
                key: item for key, item in investigation.items() if key in allowed
            }
            analysis = value.get("analysis", {})
            value["analysis"] = {
                key: analysis.get(key)
                for key in (
                    "category",
                    "severity",
                    "title",
                    "requires_onsite",
                    "cc_required",
                    "cc_reason",
                    "suggestions",
                    "missing_information",
                    "facility_assessment",
                )
                if key in analysis
            }
        elif normalized_role == "facility_lead":
            investigation.pop("knowledge_retrieval", None)
            investigation.pop("prompt_contracts", None)
            investigation.pop("rule_matches", None)
            investigation.pop("extracted_facts", None)
        elif normalized_role == "interface_person":
            investigation.pop("prompt_contracts", None)
            retrieval = investigation.get("knowledge_retrieval")
            if isinstance(retrieval, dict):
                retrieval["cards"] = [
                    {
                        key: card.get(key)
                        for key in (
                            "id",
                            "version",
                            "title",
                            "score",
                            "retrieval_reasons",
                            "prohibited_inferences",
                            "stop_conditions",
                        )
                        if key in card
                    }
                    for card in retrieval.get("cards", [])
                ]
    return value


def project_agent_run(run: Mapping[str, Any]) -> Dict[str, Any]:
    """Keep the explainable trace while hiding raw provider exchanges by default."""

    value = _redact_value(copy.deepcopy(dict(run)))
    for step in value.get("steps", []):
        output = step.get("tool_output")
        if isinstance(output, dict):
            for record in output.get("records", []):
                if isinstance(record, dict):
                    record.pop("raw_payload", None)
                    record.pop("normalized", None)
        model_output = step.get("model_output")
        if isinstance(model_output, dict):
            trace = model_output.get("trace")
            if isinstance(trace, dict):
                trace.pop("messages", None)
                trace.pop("raw_response", None)
                trace["raw_available_by_break_glass"] = True
    value["access_scope"] = {
        "default_view": "structured_redacted",
        "raw_requires_break_glass": True,
    }
    return value


def project_integration_event(event: Mapping[str, Any]) -> Dict[str, Any]:
    value = _redact_value(copy.deepcopy(dict(event)))
    value.pop("raw_payload", None)
    value.pop("normalized", None)
    value["raw_available_by_break_glass"] = True
    return value
