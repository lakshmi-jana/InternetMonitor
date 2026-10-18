import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from internet_monitor.config import AppConfig, DashboardSettings
from internet_monitor.database import Database
from internet_monitor.server import DashboardServer


class FakeScheduler:
    def __init__(self) -> None:
        self.callbacks = []
        self.requested = False

    def subscribe(self, callback) -> None:
        self.callbacks.append(callback)

    def status(self) -> dict:
        return {"test_running": False, "connectivity": None, "speed_test": None}

    def request_speed_test(self) -> bool:
        self.requested = True
        return True


class DashboardServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temp_dir.name) / "server.db")
        self.database.initialize()
        self.scheduler = FakeScheduler()
        self.config = AppConfig(
            dashboard=DashboardSettings(
                port=0, lan_enabled=False, access_token="test-token"
            )
        )
        self.server = DashboardServer(
            self.config, self.database, self.scheduler  # type: ignore[arg-type]
        )
        self.server.start()
        self.base_url = f"http://127.0.0.1:{self.server._server.server_port}"

    def tearDown(self) -> None:
        self.server.stop()
        self.temp_dir.cleanup()

    def test_serves_dashboard_and_api(self) -> None:
        with urllib.request.urlopen(self.base_url, timeout=2) as response:
            html = response.read().decode()
        self.assertIn("Internet Connection Monitor", html)

        with urllib.request.urlopen(
            f"{self.base_url}/api/status", timeout=2
        ) as response:
            status = json.load(response)
        self.assertFalse(status["test_running"])

        request = urllib.request.Request(
            f"{self.base_url}/api/run-test", method="POST"
        )
        with urllib.request.urlopen(request, timeout=2) as response:
            self.assertEqual(response.status, 202)
        self.assertTrue(self.scheduler.requested)

        with urllib.request.urlopen(
            f"{self.base_url}/api/logs?failures=1&hours=24", timeout=2
        ) as response:
            logs = json.load(response)
        self.assertEqual(logs["total"], 0)
        self.assertEqual(logs["items"], [])

    def test_lan_mode_requires_access_token(self) -> None:
        self.server.stop()
        self.config.dashboard.lan_enabled = True
        self.server = DashboardServer(
            self.config, self.database, self.scheduler  # type: ignore[arg-type]
        )
        self.server.start()
        self.base_url = f"http://127.0.0.1:{self.server._server.server_port}"

        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(f"{self.base_url}/api/status", timeout=2)
        self.assertEqual(error.exception.code, 401)

        with urllib.request.urlopen(
            f"{self.base_url}/api/status?token=test-token", timeout=2
        ) as response:
            self.assertEqual(response.status, 200)


if __name__ == "__main__":
    unittest.main()
