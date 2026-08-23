import unittest

from idcops.models import NormalizedInput
from idcops.rules import analyze_rules
from idcops.security import redact_text
from idcops.service import normalize_input


class RuleAnalysisTests(unittest.TestCase):
    def test_disk_log_keeps_full_sn_and_requires_onsite(self):
        event = normalize_input(
            "log",
            {
                "site": "BJYZ",
                "sn": "FULL-SERIAL-20260823-0099",
                "rack_position": "BJYZD2SC-B-09-03",
                "device_type": "server",
                "summary": "磁盘异常",
                "log_text": "kernel: blk_update_request: I/O error, dev nvme1n1",
            },
        )
        analysis = analyze_rules(event)
        self.assertEqual(event.device.sn, "FULL-SERIAL-20260823-0099")
        self.assertEqual(analysis.category, "hardware")
        self.assertTrue(analysis.requires_onsite)

    def test_oom_is_system_issue_not_physical_memory_failure(self):
        event = NormalizedInput.from_mapping(
            {
                "source": "log",
                "summary": "服务被系统杀死",
                "raw_text": "kernel: Out of memory: Killed process 2019 (java)",
                "device": {"sn": "OOM-SN-001", "rack_position": "A-01"},
            }
        )
        analysis = analyze_rules(event)
        self.assertEqual(analysis.category, "system")
        self.assertFalse(analysis.requires_onsite)

    def test_room_word_does_not_match_oom_abbreviation(self):
        event = normalize_input(
            "monitor",
            {
                "site": "UNKNOWN-DEMO",
                "device_type": "facility",
                "summary": "双路供电中断",
                "message": "feed A lost; feed B lost; room criticality unavailable",
            },
        )
        analysis = analyze_rules(event)
        self.assertEqual(analysis.category, "facility")
        self.assertNotIn("system_memory_pressure", analysis.matched_rules)

    def test_temperature_never_implies_cc_without_explicit_signal(self):
        event = NormalizedInput.from_mapping(
            {
                "source": "monitor",
                "summary": "机房高温",
                "raw_text": "temperature critical 35C; fan full speed",
                "labels": {},
            }
        )
        self.assertFalse(analyze_rules(event).cc_required)
        event.labels["cc_required"] = True
        self.assertTrue(analyze_rules(event).cc_required)

    def test_redaction_preserves_device_identity(self):
        text, counts = redact_text(
            "sn=SERVER-SN-001 api_key=topsecret email=user@example.com Authorization: Bearer abc.def"
        )
        self.assertIn("SERVER-SN-001", text)
        self.assertNotIn("topsecret", text)
        self.assertNotIn("user@example.com", text)
        self.assertNotIn("abc.def", text)
        self.assertGreaterEqual(sum(counts.values()), 3)


if __name__ == "__main__":
    unittest.main()
