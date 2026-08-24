import tempfile
import unittest
from pathlib import Path

from idcops.agent_tools import AgentToolRegistry
from idcops.lab import IntegrationLab
from idcops.store import IncidentStore


class AgentToolTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.lab = IntegrationLab(IncidentStore(str(Path(self.tempdir.name) / "tools.db")))
        self.tools = AgentToolRegistry(self.lab)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_all_registered_tools_are_read_only(self):
        tools = self.tools.list_tools()
        self.assertEqual(len(tools), 7)
        self.assertTrue(all(item["read_only"] for item in tools))

    def test_unknown_tool_and_write_parameter_are_rejected(self):
        with self.assertRaises(ValueError):
            self.tools.execute("server.reboot", {})
        with self.assertRaises(ValueError):
            self.tools.execute("bmc.query_health", {"power_action": "off"})


if __name__ == "__main__":
    unittest.main()
