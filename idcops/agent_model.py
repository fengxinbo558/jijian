"""OpenAI-compatible planner for an evidence-bound, read-only investigation loop."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any, Dict, Mapping, Sequence

from .ai import AIEnricher
from .security import redact_text


class AgentModelError(RuntimeError):
    """Raised when a real model cannot produce a valid investigation decision."""


class OpenAICompatibleAgentPlanner:
    """Ask a configured model for one auditable decision at a time.

    The model is never asked to infer device identity. Every tool call is scoped by
    the already-created incident ID; identity and topology remain deterministic.
    """

    prompt_version = "agent-investigator-v1"

    def __init__(self, ai: AIEnricher) -> None:
        self.ai = ai

    def propose(
        self,
        incident: Mapping[str, Any],
        hypotheses: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
        history: Sequence[Mapping[str, Any]],
        round_no: int,
    ) -> Dict[str, Any]:
        evidence = []
        allowed_evidence_ids = set()
        for item in incident.get("evidence", []):
            evidence_id = str(item.get("id") or "")
            if not evidence_id:
                continue
            allowed_evidence_ids.add(evidence_id)
            safe_text, _counts = redact_text(str(item.get("text") or ""))
            evidence.append({"id": evidence_id, "text": safe_text[:800]})

        safe_history = []
        for item in history[-6:]:
            record_ids = [str(value) for value in item.get("record_ids", []) if value]
            allowed_evidence_ids.update(record_ids)
            safe_history.append(
                {
                    "round": item.get("round"),
                    "tool": item.get("tool"),
                    "state": item.get("state"),
                    "record_ids": record_ids,
                    "record_count": item.get("record_count", 0),
                    "platform_message": str(item.get("platform_message") or "")[:300],
                }
            )

        safe_summary, _counts = redact_text(str(incident.get("summary") or ""))
        allowed_tools = {str(item["name"]) for item in tools}
        package = {
            "round": round_no,
            "incident": {
                "id": str(incident.get("id") or ""),
                "category": str(incident.get("category") or "unknown"),
                "severity": str(incident.get("severity") or "unknown"),
                "summary": safe_summary[:800],
            },
            "evidence": evidence,
            "current_hypotheses": list(hypotheses)[:8],
            "previous_tool_results": safe_history,
            "allowed_read_only_tools": list(tools),
        }
        system = (
            "你是IDC故障只读调查Agent。只能依据给定证据和工具结果工作；"
            "不得猜测SN、机架位、端口、拓扑、操作许可或责任人；不得建议把高风险动作自动执行。"
            "每轮只选择一个只读工具，或停止。输出一个JSON对象，不输出隐藏思维链。"
        )
        user = (
            "输出字段：rationale（简短可审计依据）、evidence_ids、hypotheses、"
            "next_action、stop、stop_reason、conclusion。hypotheses每项含title、status、"
            "evidence_ids、counter_evidence；status只能是candidate或high_likelihood。"
            "next_action在继续时必须是{tool,args}，args只允许incident_id和limit；"
            "停止时next_action必须为null。不得把候选原因写成已确认。\n\n"
            + json.dumps(package, ensure_ascii=False)
        )
        request_body = {
            "model": self.ai.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.1,
        }
        headers = {"Content-Type": "application/json"}
        if self.ai.api_key:
            headers["Authorization"] = f"Bearer {self.ai.api_key}"
        request = urllib.request.Request(
            self.ai.url,
            data=json.dumps(request_body).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.ai.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            raw_content = payload["choices"][0]["message"]["content"]
            parsed = self._parse_json(raw_content)
            result = self._validate(
                parsed,
                allowed_tools=allowed_tools,
                allowed_evidence_ids=allowed_evidence_ids,
                incident_id=str(incident.get("id") or ""),
            )
            result["_trace"] = {
                "provider": self.ai.url,
                "model": self.ai.model,
                "prompt_version": self.prompt_version,
                "messages": request_body["messages"],
                "raw_response": str(raw_content)[:12000],
                "validation": "accepted",
            }
            return result
        except (
            KeyError,
            IndexError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
            urllib.error.URLError,
            TimeoutError,
        ) as exc:
            raise AgentModelError("模型调用或结构校验失败") from exc

    @staticmethod
    def _parse_json(content: Any) -> Mapping[str, Any]:
        text = str(content).strip()
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
        value = json.loads(text)
        if not isinstance(value, Mapping):
            raise ValueError("agent output is not an object")
        return value

    @staticmethod
    def _validate(
        value: Mapping[str, Any],
        allowed_tools: set,
        allowed_evidence_ids: set,
        incident_id: str,
    ) -> Dict[str, Any]:
        stop = bool(value.get("stop"))
        references = [
            str(item)
            for item in value.get("evidence_ids", [])
            if str(item) in allowed_evidence_ids
        ]
        hypotheses = []
        for item in value.get("hypotheses", [])[:8]:
            if not isinstance(item, Mapping):
                continue
            item_references = [
                str(reference)
                for reference in item.get("evidence_ids", [])
                if str(reference) in allowed_evidence_ids
            ]
            if not item_references:
                continue
            status = str(item.get("status") or "candidate")
            if status not in {"candidate", "high_likelihood"}:
                status = "candidate"
            hypotheses.append(
                {
                    "title": str(item.get("title") or "待确认原因")[:300],
                    "status": status,
                    "evidence_ids": item_references,
                    "counter_evidence": str(item.get("counter_evidence") or "未提供")[:500],
                    "generated_by": "real_model_agent",
                }
            )

        next_action = value.get("next_action")
        if stop:
            next_action = None
        else:
            if not isinstance(next_action, Mapping):
                raise ValueError("missing next action")
            tool_name = str(next_action.get("tool") or "")
            if tool_name not in allowed_tools:
                raise ValueError("tool is not allow-listed")
            raw_args = next_action.get("args")
            raw_args = raw_args if isinstance(raw_args, Mapping) else {}
            unknown = set(str(key) for key in raw_args) - {"incident_id", "limit"}
            if unknown:
                raise ValueError("tool arguments contain forbidden fields")
            next_action = {
                "tool": tool_name,
                "args": {
                    "incident_id": incident_id,
                    "limit": max(1, min(int(raw_args.get("limit") or 50), 200)),
                },
            }
        return {
            "rationale": str(value.get("rationale") or "模型未提供可审计依据")[:1000],
            "evidence_ids": references,
            "hypotheses": hypotheses,
            "next_action": next_action,
            "stop": stop,
            "stop_reason": str(value.get("stop_reason") or ("model_stop" if stop else ""))[:200],
            "conclusion": str(value.get("conclusion") or "")[:1000],
        }
