from __future__ import annotations

import argparse
import ctypes
import signal
import threading
import urllib.parse
import webbrowser
from pathlib import Path

from .config import data_directory, load_config
from .database import Database
from .probes import ConnectivityProbe, HttpSpeedTest
from .scheduler import MonitoringScheduler
from .server import DashboardServer
from .tray import SingleInstance, TrayApplication


def run() -> int:
    parser = argparse.ArgumentParser(description="Internet Connection Monitor")
    parser.add_argument("--config", type=Path, help="Path to a JSON configuration file")
    parser.add_argument(
        "--no-tray", action="store_true", help="Run in the console without a tray icon"
    )
    args = parser.parse_args()

    config, config_path = load_config(args.config)
    instance = SingleInstance()
    base_url = f"http://127.0.0.1:{config.dashboard.port}"
    if config.dashboard.lan_enabled:
        base_url += (
            f"/?token={urllib.parse.quote(config.dashboard.access_token)}"
        )
    if not instance.is_first:
        webbrowser.open(base_url)
        instance.close()
        return 0

    database = Database(data_directory() / "history.db")
    database.initialize()
    scheduler = MonitoringScheduler(
        config,
        database,
        ConnectivityProbe(config.connectivity),
        HttpSpeedTest(config.speed),
        config_path,
    )

    try:
        server = DashboardServer(config, database, scheduler)
    except OSError as exc:
        instance.close()
        _show_error(
            f"Could not start the dashboard on port {config.dashboard.port}.\n\n{exc}"
        )
        return 1

    stopped = False
    stop_lock = threading.Lock()

    def shutdown() -> None:
        nonlocal stopped
        with stop_lock:
            if stopped:
                return
            stopped = True
        scheduler.stop()
        server.stop()
        instance.close()

    server.start()
    scheduler.start()

    if args.no_tray:
        finished = threading.Event()

        def handle_signal(*_args) -> None:
            finished.set()

        signal.signal(signal.SIGINT, handle_signal)
        signal.signal(signal.SIGTERM, handle_signal)
        print(f"Internet Monitor is running at {server.local_url}")
        print(f"Configuration: {config_path}")
        print("Press Ctrl+C to stop.")
        try:
            finished.wait()
        finally:
            shutdown()
        return 0

    tray = TrayApplication(server.local_url, scheduler.request_speed_test, shutdown)
    tray.open_dashboard()
    try:
        tray.run()
    finally:
        shutdown()
    return 0


def _show_error(message: str) -> None:
    ctypes.windll.user32.MessageBoxW(
        None, message, "Internet Connection Monitor", 0x10
    )
