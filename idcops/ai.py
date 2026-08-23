"""Optional OpenAI-compatible model enrichment with fail-safe degradation."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Any, Dict, Mapping, Optional

from .models import NormalizedInput, RuleAnalysis
from .security import redact_text


class AIEnricher:
    def __init__(self) -> None:
        self.url = os.getenv("IDCAI_MODEL_URL", "").strip()
        self.model = os.getenv("IDCAI_MODEL", "").strip()
        self.api_key = os.getenv("IDCAI_API_KEY", "").strip()
        self.allow_external = os.getenv("IDCAI_ALLOW_EXTERNAL", "0").strip() == "1"
        self.timeout = float(os.getenv("IDCAI_MODEL_TIMEOUT", "12"))

    @property
    def enabled(self) -> bool:
        return bool(self.allow_external and self.url and self.model)

    def enrich(
        self, event: NormalizedInput, analysis: RuleAnalysis
    ) -> Optional[Dict[str, Any]]:
        if not self.enabled:
            return None
        redacted, _counts = redact_text(event.raw_text)
        safe_summary, _summary_counts = redact_text(event.summary)
        evidence_ids = {item["id"] for item in analysis.evidence}
        safe_analysis = analysis.to_dict()
        for evidence in safe_analysis.get("evidence", []):
            evidence["text"], _evidence_counts = redact_text(str(evidence.get("text", "")))
        package = {
            "site": event.site,
            "device": event.device.to_dict(),
            "summary": safe_summary,
            "redacted_log_excerpt": redacted[:8000],
            "rule_analysis": safe_analysis,
        }
        prompt = (
            "你是私有化IDC故障调查员。只依据提供的证据输出JSON，不得猜测设备身份、"
            "机架位、电源许可或根因。JSON字段仅允许 impact_summary、candidate_causes、"
            "suggestions、missing_information。candidate_causes每项必须含title、confidence、"
            "evidence_ids、counter_evidence、status；evidence_ids只能引用现有ID。\n\n"
            + json.dumps(package, ensure_ascii=False)
        )
        request_body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "只输出一个JSON对象。"},
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
            return self._validate(result, evidence_ids)
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
        for key in ("suggestions", "missing_information"):
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
                confidence = float(item.get("confidence", 0.2))
                if not references:
                    confidence = min(confidence, 0.35)
                candidates.append(
                    {
                        "title": str(item.get("title", "待确认原因"))[:300],
                        "confidence": max(0.0, min(confidence, 1.0)),
                        "evidence_ids": references,
                        "counter_evidence": str(item.get("counter_evidence", "未提供"))[:500],
                        "status": "候选" if references else "待确认",
                    }
                )
        if candidates:
            result["candidate_causes"] = candidates
        return result
