"""The deployment supervisor can restart children without exiting."""
import unittest
from unittest.mock import patch

from scripts import run_deployment


class Child:
    def __init__(self, pid=100):
        self.pid = pid
        self.stopped = False

    def poll(self):
        return 0 if self.stopped else None

    def terminate(self):
        self.stopped = True

    def wait(self, timeout=None):
        return 0


class DeploymentRestartTests(unittest.TestCase):
    def test_web_mode_bot_restarts_web_process(self):
        original = Child()
        replacement = Child(200)
        processes = {"rasa": Child(), "actions": Child(), "web": original}
        with patch.object(run_deployment, "commands", return_value={"web": ["python", "web"]}), \
             patch.object(run_deployment, "_start_service", return_value=replacement) as spawn, \
             patch.object(run_deployment, "_report"):
            result = run_deployment.restart_services("bot", processes, "web")
        self.assertEqual(result, {"web": 200})
        self.assertTrue(original.stopped)
        self.assertIs(processes["web"], replacement)
        self.assertIsNone(processes["rasa"].poll())
        spawn.assert_called_once()

    def test_rasa_restart_leaves_web_running(self):
        original = Child()
        web = Child()
        processes = {"rasa": original, "web": web}
        with patch.object(run_deployment, "commands", return_value={"rasa": ["python", "rasa"]}), \
             patch.object(run_deployment, "_start_service", return_value=Child(300)), \
             patch.object(run_deployment, "_report"):
            result = run_deployment.restart_services("rasa", processes, "web")
        self.assertEqual(result, {"rasa": 300})
        self.assertIsNone(web.poll())

    def test_unavailable_service_is_rejected_before_stopping_anything(self):
        rasa = Child()
        with self.assertRaisesRegex(ValueError, "jupyter is not running"):
            run_deployment.restart_services("jupyter", {"rasa": rasa}, "bot")
        self.assertIsNone(rasa.poll())


if __name__ == "__main__":
    unittest.main()
