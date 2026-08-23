import json
import os
import unittest
from unittest.mock import patch

from idcops.ai import AIEnricher
from idcops.models import NormalizedInput
from idcops.rules import analyze_rules


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class AIEnricherTests(unittest.TestCase):
    def event_and_analysis(self):
        event = NormalizedInput.from_mapping(
            {
                "source": "log",
                "site": "BJYZ",
                "device": {"sn": "AI-TEST-SN-001", "rack_position": "RACK-A-01"},
                "summary": "磁盘错误 password=summarysecret",
                "raw_text": "I/O error, dev sdb; api_key=must-not-leave",
            }
        )
        return event, analyze_rules(event)

    def test_disabled_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            enricher = AIEnricher()
            event, analysis = self.event_and_analysis()
            self.assertFalse(enricher.enabled)
            self.assertIsNone(enricher.enrich(event, analysis))

    def test_enabled_model_receives_redacted_evidence_and_rejects_unknown_ids(self):
        response = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "impact_summary": "单机磁盘风险",
                                "candidate_causes": [
                                    {
                                        "title": "磁盘介质异常",
                                        "evidence_ids": ["E1"],
                                        "counter_evidence": "缺少SMART完整结果",
                                        "status": "confirmed",
                                    },
                                    {
                                        "title": "无证据猜测",
                                        "evidence_ids": ["E999"],
                                        "counter_evidence": "无",
                                        "status": "high_likelihood",
                                    },
                                ],
                            },
                            ensure_ascii=False,
                        )
                    }
                }
            ]
        }
        captured = {}

        def fake_urlopen(request, timeout):
            captured["body"] = request.data.decode("utf-8")
            captured["timeout"] = timeout
            return FakeResponse(response)

        environment = {
            "IDCAI_ALLOW_EXTERNAL": "1",
            "IDCAI_MODEL_URL": "http://private-model/v1/chat/completions",
            "IDCAI_MODEL": "local-model",
        }
        with patch.dict(os.environ, environment, clear=True), patch(
            "urllib.request.urlopen", side_effect=fake_urlopen
        ):
            enricher = AIEnricher()
            event, analysis = self.event_and_analysis()
            result = enricher.enrich(event, analysis)

        self.assertIsNotNone(result)
        self.assertNotIn("must-not-leave", captured["body"])
        self.assertNotIn("summarysecret", captured["body"])
        self.assertNotIn("AI-TEST-SN-001", captured["body"])
        self.assertIn("[REDACTED]", captured["body"])
        self.assertEqual(result["candidate_causes"][0]["status"], "candidate")
        self.assertEqual(len(result["candidate_causes"]), 1)
        self.assertNotIn("confidence", result["candidate_causes"][0])


if __name__ == "__main__":
    unittest.main()
