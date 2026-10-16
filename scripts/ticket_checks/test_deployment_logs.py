import io
from contextlib import redirect_stdout
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts import run_deployment


class DeploymentLogTests(unittest.TestCase):
    def test_supervisor_and_child_output_reach_files_and_console(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(run_deployment, 'LOG_DIR', Path(folder)):
            console = io.StringIO()
            with redirect_stdout(console):
                run_deployment._report('[deployment] starting actions')
                run_deployment._relay_output(
                    'actions', io.StringIO('Darwinbox holiday configuration: missing mapping\n'),
                    run_deployment._open_log('actions'),
                )
            self.assertIn('starting actions', (Path(folder) / 'deployment.log').read_text())
            self.assertIn('missing mapping', (Path(folder) / 'actions.log').read_text())
            self.assertIn('missing mapping', console.getvalue())

    def test_started_service_writes_its_output(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(run_deployment, 'LOG_DIR', Path(folder)):
            with redirect_stdout(io.StringIO()):
                process = run_deployment._start_service(
                    'actions', [sys.executable, '-c', "print('worker ready')"],
                )
                self.assertEqual(process.wait(timeout=5), 0)
                process.log_relay.join(timeout=5)
            self.assertIn('worker ready', (Path(folder) / 'actions.log').read_text())


if __name__ == '__main__':
    unittest.main()
