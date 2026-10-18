from __future__ import annotations

import json
import os
import secrets
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass(slots=True)
class SpeedSettings:
    interval_minutes: int = 60
    interval_until: str | None = None
    restore_interval_minutes: int = 60
    download_url: str = "https://speed.cloudflare.com/__down"
    upload_url: str = "https://speed.cloudflare.com/__up"
    download_bytes_per_stream: int = 5_000_000
    download_streams: int = 4
    upload_bytes: int = 5_000_000
    timeout_seconds: int = 30


@dataclass(slots=True)
class ConnectivitySettings:
    interval_seconds: int = 30
    dns_host: str = "api.anthropic.com"
    probe_url: str = "https://api.anthropic.com/"
    attempts: int = 3
    timeout_seconds: int = 5
    ping_host: str = "api.anthropic.com"
    ping_count: int = 10
    ping_timeout_ms: int = 1000


@dataclass(slots=True)
class DashboardSettings:
    port: int = 8765
    lan_enabled: bool = False
    access_token: str = field(default_factory=lambda: secrets.token_urlsafe(24))


@dataclass(slots=True)
class AppConfig:
    retention_days: int = 90
    run_speed_test_on_startup: bool = True
    speed: SpeedSettings = field(default_factory=SpeedSettings)
    connectivity: ConnectivitySettings = field(default_factory=ConnectivitySettings)
    dashboard: DashboardSettings = field(default_factory=DashboardSettings)

    @property
    def bind_host(self) -> str:
        return "0.0.0.0" if self.dashboard.lan_enabled else "127.0.0.1"


def data_directory() -> Path:
    root = os.environ.get("LOCALAPPDATA")
    path = Path(root) / "InternetMonitor" if root else Path.home() / ".internet-monitor"
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_config(path: Path | None = None) -> tuple[AppConfig, Path]:
    config_path = path or data_directory() / "config.json"
    if not config_path.exists():
        config = AppConfig()
        _write_config(config_path, config)
        return config, config_path

    raw = json.loads(config_path.read_text(encoding="utf-8"))
    config = AppConfig(
        retention_days=int(raw.get("retention_days", 90)),
        run_speed_test_on_startup=bool(raw.get("run_speed_test_on_startup", True)),
        speed=SpeedSettings(**raw.get("speed", {})),
        connectivity=ConnectivitySettings(**raw.get("connectivity", {})),
        dashboard=DashboardSettings(**raw.get("dashboard", {})),
    )
    if not config.dashboard.access_token:
        config.dashboard.access_token = secrets.token_urlsafe(24)
        _write_config(config_path, config)
    return config, config_path


def save_config(path: Path, config: AppConfig) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(config), indent=2), encoding="utf-8")


def _write_config(path: Path, config: AppConfig) -> None:
    save_config(path, config)
