import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from internet_monitor.database import Database
from internet_monitor.models import ConnectivityResult, SpeedResult


class DatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temp_dir.name) / "test.db")
        self.database.initialize()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_records_and_reads_latest_results(self) -> None:
        checked_at = datetime.now(UTC).isoformat()
        self.database.add_connectivity(
            ConnectivityResult(
                checked_at, True, 12.5, 1.2, 0, 3.5,
                packet_loss_percent=10, packets_sent=10, packets_lost=1,
            )
        )
        self.database.add_speed_test(
            SpeedResult(checked_at, 123.4, 45.6, 2.1, "local.test")
        )

        latest = self.database.latest_connectivity()
        self.assertTrue(latest["online"])
        self.assertEqual(latest["packet_loss_percent"], 10)
        self.assertEqual(
            self.database.latest_speed_test()["download_mbps"], 123.4
        )
        history = self.database.history(24)
        self.assertEqual(len(history["connectivity"]), 1)
        self.assertEqual(len(history["speed_tests"]), 1)

    def test_cleanup_removes_only_expired_rows(self) -> None:
        old = (datetime.now(UTC) - timedelta(days=100)).isoformat()
        current = datetime.now(UTC).isoformat()
        self.database.add_connectivity(
            ConnectivityResult(old, False, None, None, 3, None, "offline")
        )
        self.database.add_connectivity(
            ConnectivityResult(current, True, 10, 1, 0, 2)
        )

        removed = self.database.cleanup(90)

        self.assertEqual(removed, 1)
        self.assertEqual(self.database.latest_connectivity()["checked_at"], current)

    def test_logs_can_return_failures_only(self) -> None:
        checked_at = datetime.now(UTC).isoformat()
        self.database.add_connectivity(
            ConnectivityResult(checked_at, True, 8, 1, 0, 2)
        )
        self.database.add_connectivity(
            ConnectivityResult(checked_at, False, None, None, 3, None, "DNS failed")
        )
        self.database.add_speed_test(
            SpeedResult(checked_at, None, None, 1, "local.test", "timeout")
        )

        failures = self.database.logs(failures_only=True, limit=10)
        everything = self.database.logs(failures_only=False, limit=1, offset=0)

        self.assertEqual(failures["total"], 2)
        self.assertTrue(all(item["failed"] for item in failures["items"]))
        self.assertEqual(everything["total"], 3)
        self.assertEqual(len(everything["items"]), 1)


if __name__ == "__main__":
    unittest.main()
