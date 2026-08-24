import tempfile
import unittest
from pathlib import Path

from idcops.service import IncidentService
from idcops.store import IncidentStore


class ReleaseTrustTests(unittest.TestCase):
    def test_release_checks_do_not_claim_factual_correctness(self):
        with tempfile.TemporaryDirectory() as directory:
            service = IncidentService(IncidentStore(str(Path(directory) / "release.db")))
            service.assets.create_prompt_version(
                "hypothesis",
                {
                    "version": "trust-v1",
                    "system_content": "只输出JSON",
                    "user_template": "分析 {{event_summary}}",
                    "variables": ["event_summary"],
                    "output_schema": ["candidate_causes"],
                    "settings": {},
                },
                "tester",
            )
            release = service.releases.test_asset(
                {"asset_type": "prompt", "asset_key": "hypothesis", "version": "trust-v1"},
                "tester",
            )
            self.assertTrue(all(item["scope"] in {"structural", "workflow"} for item in release["test_summary"]))
            self.assertTrue(all(item["does_not_prove"] for item in release["test_summary"]))
            self.assertTrue(all("结构" in item["name"] or "流程" in item["name"] for item in release["test_summary"]))


if __name__ == "__main__":
    unittest.main()
