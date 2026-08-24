"""Optional OpenAI-compatible model enrichment with fail-safe degradation."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from .assets import AssetRegistry
from .models import NormalizedInput, RuleAnalysis
from .security import redact_text


class AIEnricher:
    def __init__(self, registry: Optional[AssetRegistry] = None) -> None:
        self.registry = registry
        self.url = os.getenv("IDCAI_MODEL_URL", "").strip()
        self.model = os.getenv("IDCAI_MODEL", "").strip()
        self.api_key = os.getenv("IDCAI_API_KEY", "").strip()
        self.allow_external = os.getenv("IDCAI_ALLOW_EXTERNAL", "0").strip() == "1"
        self.timeout = float(os.getenv("IDCAI_MODEL_TIMEOUT", "12"))
        contract_path = Path(__file__).resolve().parent.parent / "prompts" / "contracts.json"
        try:
            contracts = json.loads(contract_path.read_text(encoding="utf-8"))
            self.hypothesis_contract = contracts["contracts"]["hypothesis"]
        except (OSError, KeyError, TypeError, json.JSONDecodeError):
            self.hypothesis_contract = {
                "version": "hypothesis-v1.0.0",
                "instructions": "只依据证据形成候选，不得猜测身份或操作许可。",
            }

    @property
    def enabled(self) -> bool:
        return bool(self.allow_external and self.url and self.model)

    def enrich(
        self,
        event: NormalizedInput,
        analysis: RuleAnalysis,
        investigation: Optional[Mapping[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        if not self.enabled:
            return None
        redacted, _counts = redact_text(event.raw_text)
        safe_summary, _summary_counts = redact_text(event.summary)
        investigation = investigation or {}
        source_evidence = investigation.get("evidence", analysis.evidence)
        evidence_ids = {str(item["id"]) for item in source_evidence if item.get("id")}
        safe_evidence = []
        for evidence in source_evidence:
            text, _evidence_counts = redact_text(str(evidence.get("text", "")))
            safe_evidence.append({"id": str(evidence.get("id", "")), "text": text[:1000]})
        safe_facts = []
        for fact in investigation.get("extracted_facts", []):
            value, _value_counts = redact_text(str(fact.get("value", "")))
            excerpt, _excerpt_counts = redact_text(str(fact.get("excerpt", "")))
            safe_facts.append(
                {
                    "id": str(fact.get("id", "")),
                    "type": str(fact.get("type", "")),
                    "value": value[:200],
                    "excerpt": excerpt[:500],
                    "evidence_ids": list(fact.get("evidence_ids", [])),
                }
            )
        package = {
            "summary": safe_summary,
            "redacted_log_excerpt": redacted[:8000],
            "evidence": safe_evidence,
            "facts": safe_facts,
            "knowledge_cards": [
                {
                    "id": card.get("id"),
                    "version": card.get("version"),
                    "title": card.get("title"),
                    "prohibited_inferences": card.get("prohibited_inferences", []),
                    "stop_conditions": card.get("stop_conditions", []),
                }
                for card in investigation.get("knowledge_retrieval", {}).get("cards", [])[:8]
            ],
            "baseline_hypotheses": investigation.get("hypotheses", [])[:8],
        }
        prompt_asset = (
            self.registry.get_published_prompt_version("hypothesis")
            if self.registry is not None
            else None
        )
        contract_version = (
            prompt_asset.get("version") if prompt_asset else self.hypothesis_contract.get("version")
        )
        instructions = (
            prompt_asset.get("user_template")
            if prompt_asset
            else self.hypothesis_contract.get("instructions")
        )
        system_content = (
            prompt_asset.get("system_content") if prompt_asset else "只输出一个JSON对象。"
        )
        prompt = (
            f"提示词契约：{contract_version}。"
            f"{instructions}"
            "只输出JSON，字段仅允许 impact_summary、candidate_causes、missing_information。"
            "candidate_causes每项必须含title、evidence_ids、counter_evidence、status；"
            "status只能是candidate或high_likelihood；evidence_ids只能引用现有ID。\n\n"
            + json.dumps(package, ensure_ascii=False)
        )
        request_body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_content},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.1,
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(
            self.url,
            data=json.dumps(request_body).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            content = payload["choices"][0]["message"]["content"]
            result = self._parse_json(content)
            validated = self._validate(result, evidence_ids)
            redacted_response, _response_counts = redact_text(str(content))
            validated["_model_trace"] = {
                "provider": self.url,
                "model": self.model,
                "prompt_version": contract_version,
                "messages": request_body["messages"],
                "raw_response": redacted_response[:12000],
                "validation": "accepted",
            }
            return validated
        except (
            KeyError,
            IndexError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
            urllib.error.URLError,
            TimeoutError,
        ):
            return None

    @staticmethod
    def _parse_json(content: Any) -> Mapping[str, Any]:
        text = str(content).strip()
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
        value = json.loads(text)
        if not isinstance(value, Mapping):
            raise ValueError("model output is not an object")
        return value

    @staticmethod
    def _validate(value: Mapping[str, Any], evidence_ids: set) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        if isinstance(value.get("impact_summary"), str):
            result["impact_summary"] = value["impact_summary"][:600]
        for key in ("missing_information",):
            items = value.get(key)
            if isinstance(items, list):
                result[key] = [str(item)[:400] for item in items[:8]]
        candidates = []
        raw_candidates = value.get("candidate_causes")
        if isinstance(raw_candidates, list):
            for item in raw_candidates[:5]:
                if not isinstance(item, Mapping):
                    continue
                references = [
                    str(reference)
                    for reference in item.get("evidence_ids", [])
                    if str(reference) in evidence_ids
                ]
                if not references:
                    continue
                status = str(item.get("status", "candidate"))
                if status not in {"candidate", "high_likelihood"}:
                    status = "candidate"
                candidates.append(
                    {
                        "title": str(item.get("title", "待确认原因"))[:300],
                        "evidence_ids": references,
                        "counter_evidence": str(item.get("counter_evidence", "未提供"))[:500],
                        "status": status,
                    }
                )
        if candidates:
            result["candidate_causes"] = candidates
        return result
