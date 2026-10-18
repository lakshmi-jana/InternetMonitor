from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Callable

from .config import AppConfig, save_config
from .database import Database
from .probes import ConnectivityProbe, HttpSpeedTest

EventCallback = Callable[[str, dict], None]


class MonitoringScheduler:
    def __init__(
        self,
        config: AppConfig,
        database: Database,
        connectivity_probe: ConnectivityProbe,
        speed_test: HttpSpeedTest,
        config_path: Path | None = None,
    ) -> None:
        self.config = config
        self.config_path = config_path
        self.database = database
        self.connectivity_probe = connectivity_probe
        self.speed_test = speed_test
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._manual_speed_test = threading.Event()
        self._thread: threading.Thread | None = None
        self._callbacks: list[EventCallback] = []
        self._state_lock = threading.Lock()
        self._state = {
            "test_running": False,
            "next_test_at": None,
            "started_at": datetime.now(UTC).isoformat(),
        }

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run, name="monitoring-scheduler", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=self.config.speed.timeout_seconds + 5)

    def request_speed_test(self) -> bool:
        with self._state_lock:
            if self._state["test_running"] or self._manual_speed_test.is_set():
                return False
            self._manual_speed_test.set()
        self._wake.set()
        return True

    def subscribe(self, callback: EventCallback) -> None:
        self._callbacks.append(callback)

    def status(self) -> dict:
        with self._state_lock:
            runtime = dict(self._state)
        runtime["connectivity"] = self.database.latest_connectivity()
        runtime["speed_test"] = self.database.latest_speed_test()
        runtime["speed_interval_minutes"] = self.config.speed.interval_minutes
        runtime["speed_interval_until"] = self.config.speed.interval_until
        runtime["probe_host"] = self.config.connectivity.dns_host
        return runtime

    def _run(self) -> None:
        connectivity_interval = max(5, self.config.connectivity.interval_seconds)
        speed_interval = self._speed_interval_seconds()
        next_connectivity = time.monotonic()
        next_speed = (
            time.monotonic()
            if self.config.run_speed_test_on_startup
            else time.monotonic() + speed_interval
        )
        next_cleanup = time.monotonic()

        while not self._stop.is_set():
            now = time.monotonic()
            if now >= next_connectivity:
                self._run_connectivity()
                next_connectivity = time.monotonic() + connectivity_interval

            manual = self._manual_speed_test.is_set()
            speed_interval = self._speed_interval_seconds()
            if manual or now >= next_speed:
                self._manual_speed_test.clear()
                succeeded = self._run_speed_test()
                retry_delay = min(300, speed_interval)
                next_speed = time.monotonic() + (
                    speed_interval if succeeded else retry_delay
                )

            if now >= next_cleanup:
                self.database.cleanup(self.config.retention_days)
                next_cleanup = time.monotonic() + 24 * 60 * 60

            next_test_delta = max(0, next_speed - time.monotonic())
            with self._state_lock:
                self._state["next_test_at"] = (
                    datetime.now(UTC) + timedelta(seconds=next_test_delta)
                ).isoformat()

            wait_for = max(
                0.1,
                min(next_connectivity, next_speed, next_cleanup) - time.monotonic(),
            )
            self._wake.wait(wait_for)
            self._wake.clear()

    def _speed_interval_seconds(self) -> int:
        speed = self.config.speed
        if speed.interval_until:
            expiry = datetime.fromisoformat(speed.interval_until)
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=UTC)
            if datetime.now(UTC) >= expiry:
                speed.interval_minutes = speed.restore_interval_minutes
                speed.interval_until = None
                if self.config_path is not None:
                    save_config(self.config_path, self.config)
        return max(60, speed.interval_minutes * 60)

    def _run_connectivity(self) -> None:
        result = self.connectivity_probe.run()
        self.database.add_connectivity(result)
        self._publish("connectivity", result.to_dict())

    def _run_speed_test(self) -> bool:
        with self._state_lock:
            self._state["test_running"] = True
        self._publish("test_state", {"test_running": True})
        try:
            result = self.speed_test.run()
            self.database.add_speed_test(result)
            self._publish("speed_test", result.to_dict())
            return result.error is None
        finally:
            with self._state_lock:
                self._state["test_running"] = False
            self._publish("test_state", {"test_running": False})

    def _publish(self, event: str, data: dict) -> None:
        for callback in tuple(self._callbacks):
            try:
                callback(event, data)
            except Exception:
                continue
