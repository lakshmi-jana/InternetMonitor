import tempfile
import threading
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from internet_monitor.config import AppConfig, load_config
from internet_monitor.models import ConnectivityResult, SpeedResult, utc_now_iso
from internet_monitor.scheduler import MonitoringScheduler


class FakeDatabase:
    def __init__(self) -> None:
        self.connectivity = None
        self.speed = None
        self.cleaned = False

    def add_connectivity(self, result) -> None:
        self.connectivity = result.to_dict()

    def add_speed_test(self, result) -> None:
        self.speed = result.to_dict()

    def latest_connectivity(self):
        return self.connectivity

    def latest_speed_test(self):
        return self.speed

    def cleanup(self, _retention_days) -> int:
        self.cleaned = True
        return 0


class FakeConnectivityProbe:
    def run(self):
        return ConnectivityResult(utc_now_iso(), True, 5, 1, 0, 1)


class FakeSpeedTest:
    def __init__(self) -> None:
        self.finished = threading.Event()
        self.calls = 0

    def run(self):
        self.calls += 1
        result = SpeedResult(utc_now_iso(), 100, 50, 0.01, "fake")
        self.finished.set()
        return result


class SchedulerTests(unittest.TestCase):
    def test_runs_startup_checks_and_rejects_duplicate_manual_request(self) -> None:
        database = FakeDatabase()
        speed_test = FakeSpeedTest()
        scheduler = MonitoringScheduler(
            AppConfig(),
            database,  # type: ignore[arg-type]
            FakeConnectivityProbe(),  # type: ignore[arg-type]
            speed_test,  # type: ignore[arg-type]
        )

        self.assertTrue(scheduler.request_speed_test())
        self.assertFalse(scheduler.request_speed_test())
        scheduler.start()
        try:
            self.assertTrue(speed_test.finished.wait(2))
            self.assertIsNotNone(database.connectivity)
            self.assertIsNotNone(database.speed)
            self.assertTrue(database.cleaned)
        finally:
            scheduler.stop()

    def test_temporary_interval_restores_after_it_expires(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            config = AppConfig()
            config.speed.interval_minutes = 10
            config.speed.restore_interval_minutes = 60
            config.speed.interval_until = (
                datetime.now(UTC) - timedelta(minutes=1)
            ).isoformat()
            scheduler = MonitoringScheduler(
                config,
                FakeDatabase(),  # type: ignore[arg-type]
                FakeConnectivityProbe(),  # type: ignore[arg-type]
                FakeSpeedTest(),  # type: ignore[arg-type]
                config_path,
            )

            self.assertEqual(scheduler._speed_interval_seconds(), 3600)

            restored, _ = load_config(config_path)
            self.assertEqual(restored.speed.interval_minutes, 60)
            self.assertIsNone(restored.speed.interval_until)


if __name__ == "__main__":
    unittest.main()
