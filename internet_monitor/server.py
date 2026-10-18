from __future__ import annotations

import json
import mimetypes
import queue
import secrets
import threading
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .config import AppConfig
from .database import Database
from .scheduler import MonitoringScheduler


def _log_query(query: dict[str, list[str]]) -> dict:
    def number(name: str, default: int) -> int:
        try:
            return int(query.get(name, [str(default)])[0])
        except ValueError:
            return default

    return {
        "hours": number("hours", 24 * 90),
        "failures_only": query.get("failures", ["1"])[0] != "0",
        "limit": number("limit", 100),
        "offset": number("offset", 0),
    }


class DashboardServer:
    def __init__(
        self, config: AppConfig, database: Database, scheduler: MonitoringScheduler
    ) -> None:
        self.config = config
        self.database = database
        self.scheduler = scheduler
        self.static_root = Path(__file__).parent / "static"
        self._clients: list[queue.Queue[tuple[str, dict]]] = []
        self._clients_lock = threading.Lock()
        self._server = ThreadingHTTPServer(
            (config.bind_host, config.dashboard.port), self._handler_factory()
        )
        self._thread: threading.Thread | None = None
        scheduler.subscribe(self.publish)

    @property
    def local_url(self) -> str:
        base = f"http://127.0.0.1:{self.config.dashboard.port}"
        if self.config.dashboard.lan_enabled:
            return (
                f"{base}/?token="
                f"{urllib.parse.quote(self.config.dashboard.access_token)}"
            )
        return base

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="dashboard-server", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        if self._thread:
            self._thread.join(timeout=5)

    def publish(self, event: str, data: dict) -> None:
        with self._clients_lock:
            clients = tuple(self._clients)
        for client in clients:
            try:
                client.put_nowait((event, data))
            except queue.Full:
                continue

    def _handler_factory(self):
        dashboard = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "InternetMonitor/1.0"

            def do_GET(self) -> None:
                route = urllib.parse.urlsplit(self.path)
                if route.path == "/api/status":
                    if not self._authorize(route):
                        return
                    self._json(dashboard.scheduler.status())
                elif route.path == "/api/history":
                    if not self._authorize(route):
                        return
                    query = urllib.parse.parse_qs(route.query)
                    try:
                        hours = int(query.get("hours", ["24"])[0])
                    except ValueError:
                        hours = 24
                    self._json(dashboard.database.history(hours))
                elif route.path == "/api/logs":
                    if not self._authorize(route):
                        return
                    query = urllib.parse.parse_qs(route.query)
                    self._json(dashboard.database.logs(**_log_query(query)))
                elif route.path == "/api/events":
                    if not self._authorize(route):
                        return
                    self._events()
                elif route.path == "/health":
                    self._json({"status": "ok"})
                else:
                    self._static(route.path)

            def do_POST(self) -> None:
                route = urllib.parse.urlsplit(self.path)
                if route.path != "/api/run-test":
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                if not self._authorize(route):
                    return
                accepted = dashboard.scheduler.request_speed_test()
                self._json(
                    {"accepted": accepted},
                    HTTPStatus.ACCEPTED if accepted else HTTPStatus.CONFLICT,
                )

            def _authorize(self, route: urllib.parse.SplitResult) -> bool:
                if not dashboard.config.dashboard.lan_enabled:
                    return True
                query = urllib.parse.parse_qs(route.query)
                query_token = query.get("token", [""])[0]
                auth = self.headers.get("Authorization", "")
                header_token = auth.removeprefix("Bearer ").strip()
                expected = dashboard.config.dashboard.access_token
                if (query_token and secrets.compare_digest(expected, query_token)) or (
                    header_token and secrets.compare_digest(expected, header_token)
                ):
                    return True
                self._json({"error": "Unauthorized"}, HTTPStatus.UNAUTHORIZED)
                return False

            def _json(
                self, payload: dict, status: HTTPStatus = HTTPStatus.OK
            ) -> None:
                body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(body)

            def _static(self, path: str) -> None:
                requested = "index.html" if path in ("", "/") else path.lstrip("/")
                candidate = (dashboard.static_root / requested).resolve()
                if dashboard.static_root.resolve() not in candidate.parents:
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                if not candidate.is_file():
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                body = candidate.read_bytes()
                content_type = mimetypes.guess_type(candidate.name)[0]
                self.send_response(HTTPStatus.OK)
                self.send_header(
                    "Content-Type", f"{content_type or 'application/octet-stream'}; charset=utf-8"
                )
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-cache")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'self'; script-src 'self'; style-src 'self'; "
                    "connect-src 'self'; img-src 'self' data:",
                )
                self.end_headers()
                self.wfile.write(body)

            def _events(self) -> None:
                client: queue.Queue[tuple[str, dict]] = queue.Queue(maxsize=20)
                with dashboard._clients_lock:
                    dashboard._clients.append(client)
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                try:
                    while True:
                        try:
                            event, data = client.get(timeout=15)
                            message = (
                                f"event: {event}\ndata: {json.dumps(data)}\n\n"
                            )
                        except queue.Empty:
                            message = ": keepalive\n\n"
                        self.wfile.write(message.encode("utf-8"))
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
                finally:
                    with dashboard._clients_lock:
                        if client in dashboard._clients:
                            dashboard._clients.remove(client)

            def log_message(self, format: str, *args) -> None:
                return

        return Handler
