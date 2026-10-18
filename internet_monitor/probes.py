from __future__ import annotations

import os
import re
import socket
import ssl
import statistics
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from .config import ConnectivitySettings, SpeedSettings
from .models import ConnectivityResult, SpeedResult, utc_now_iso

USER_AGENT = "InternetMonitor/1.0"
_PING_STATS = re.compile(
    r"Sent = (\d+), Received = \d+, Lost = (\d+)", re.IGNORECASE
)


def parse_ping_statistics(output: str) -> tuple[int, int, float] | None:
    match = _PING_STATS.search(output)
    if match is None:
        return None
    sent = int(match.group(1))
    lost = int(match.group(2))
    if sent <= 0:
        return None
    return sent, lost, round(lost * 100 / sent, 1)


def measure_packet_loss(
    host: str, count: int, timeout_ms: int
) -> tuple[int | None, int | None, float | None, str | None]:
    count = max(1, min(count, 20))
    timeout_ms = max(100, timeout_ms)
    command = ["ping", "-n", str(count), "-w", str(timeout_ms), host]
    options = {}
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=(count * ((timeout_ms / 1000) + 1)) + 5,
            check=False,
            **options,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, None, None, f"Ping: {exc}"

    parsed = parse_ping_statistics(completed.stdout or "")
    if parsed is None:
        lines = (completed.stdout or completed.stderr or "no statistics").splitlines()
        detail = next((line.strip() for line in reversed(lines) if line.strip()), "no statistics")
        return None, None, None, f"Ping: {detail}"
    sent, lost, percent = parsed
    if lost == 0:
        return sent, lost, percent, None
    return sent, lost, percent, f"Ping: lost {lost} of {sent}"


def throughput_mbps(byte_count: int, seconds: float) -> float:
    if byte_count < 0 or seconds <= 0:
        raise ValueError("byte_count must be non-negative and seconds must be positive")
    return (byte_count * 8) / seconds / 1_000_000


class ConnectivityProbe:
    def __init__(self, settings: ConnectivitySettings, ping_probe=measure_packet_loss) -> None:
        self.settings = settings
        self.ping_probe = ping_probe

    def run(self) -> ConnectivityResult:
        dns_ms: float | None = None
        errors: list[str] = []
        try:
            started = time.perf_counter()
            socket.getaddrinfo(self.settings.dns_host, 443, type=socket.SOCK_STREAM)
            dns_ms = (time.perf_counter() - started) * 1000
        except OSError as exc:
            errors.append(f"DNS: {exc}")

        latencies, request_errors, ping_host = self._probe_requests()
        errors.extend(request_errors)
        failed = max(0, self.settings.attempts - len(latencies))
        sent, lost, packet_loss, ping_error = self.ping_probe(
            ping_host,
            self.settings.ping_count,
            self.settings.ping_timeout_ms,
        )
        messages = errors[-2:]
        if ping_error:
            messages.append(ping_error)
        return ConnectivityResult(
            checked_at=utc_now_iso(),
            online=bool(latencies),
            latency_ms=round(statistics.mean(latencies), 2) if latencies else None,
            jitter_ms=(
                round(statistics.pstdev(latencies), 2) if len(latencies) > 1 else 0.0
            )
            if latencies
            else None,
            failed_probes=failed,
            dns_ms=round(dns_ms, 2) if dns_ms is not None else None,
            error="; ".join(messages) or None,
            packet_loss_percent=packet_loss,
            packets_sent=sent,
            packets_lost=lost,
        )

    def _probe_requests(self) -> tuple[list[float], list[str], str]:
        parsed = urllib.parse.urlsplit(self.settings.probe_url)
        host = parsed.hostname or self.settings.dns_host
        if parsed.scheme == "https":
            return self._probe_https(host)
        latencies: list[float] = []
        errors: list[str] = []
        for _ in range(max(1, self.settings.attempts)):
            try:
                request = urllib.request.Request(
                    self.settings.probe_url,
                    headers={"User-Agent": USER_AGENT, "Cache-Control": "no-cache"},
                )
                started = time.perf_counter()
                with urllib.request.urlopen(
                    request, timeout=self.settings.timeout_seconds
                ) as response:
                    response.read(1)
                latencies.append((time.perf_counter() - started) * 1000)
            except urllib.error.HTTPError:
                latencies.append((time.perf_counter() - started) * 1000)
            except (OSError, TimeoutError) as exc:
                errors.append(f"HTTPS: {exc}")
        return latencies, errors, self.settings.ping_host

    def _probe_https(self, host: str) -> tuple[list[float], list[str], str]:
        try:
            targets = resolve_targets(host)
        except OSError as exc:
            return [], [f"DNS: {exc}"], self.settings.ping_host
        if not targets:
            return [], ["DNS: no addresses found"], self.settings.ping_host

        latencies: list[float] = []
        errors: list[str] = []
        reachable: list[str] = []
        with ThreadPoolExecutor(max_workers=len(targets)) as executor:
            checked = list(
                zip(
                    targets,
                    executor.map(lambda item: self._https_once(host, item[1]), targets),
                    strict=True,
                )
            )
        for (_family, address), (elapsed, error) in checked:
            if elapsed is None:
                errors.append(f"{address}: {error}")
            else:
                latencies.append(elapsed)
                reachable.append(address)

        preferred = next(
            (address for family, address in targets if address in reachable and family == socket.AF_INET),
            reachable[0] if reachable else None,
        )
        for _ in range(max(0, self.settings.attempts - 1)):
            if preferred is None:
                break
            elapsed, error = self._https_once(host, preferred)
            if elapsed is None:
                errors.append(f"{preferred}: {error}")
                break
            latencies.append(elapsed)
        if latencies:
            errors.clear()
        return latencies, errors, preferred or self.settings.ping_host

    def _https_once(self, host: str, address: str) -> tuple[float | None, str | None]:
        started = time.perf_counter()
        try:
            raw = socket.create_connection((address, 443), timeout=self.settings.timeout_seconds)
            tls = ssl.create_default_context().wrap_socket(raw, server_hostname=host)
            try:
                tls.sendall(
                    (
                        f"HEAD / HTTP/1.1\r\nHost: {host}\r\n"
                        f"User-Agent: {USER_AGENT}\r\nConnection: close\r\n\r\n"
                    ).encode()
                )
                data = tls.recv(128)
            finally:
                tls.close()
            if not data.startswith(b"HTTP/"):
                raise OSError("API did not return an HTTP response")
            return (time.perf_counter() - started) * 1000, None
        except (OSError, TimeoutError) as exc:
            return None, str(exc)


def resolve_targets(host: str) -> list[tuple[socket.AddressFamily, str]]:
    targets: list[tuple[socket.AddressFamily, str]] = []
    seen: set[str] = set()
    for family, _, _, _, sockaddr in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM):
        address = sockaddr[0]
        if address in seen:
            continue
        seen.add(address)
        targets.append((family, address))
    targets.sort(key=lambda item: item[0] != socket.AF_INET)
    return targets


class HttpSpeedTest:
    def __init__(self, settings: SpeedSettings) -> None:
        self.settings = settings

    def run(self) -> SpeedResult:
        started = time.perf_counter()
        try:
            warmup_bytes = min(100_000, self.settings.download_bytes_per_stream)
            self._download(warmup_bytes)

            with ThreadPoolExecutor(
                max_workers=max(1, self.settings.download_streams)
            ) as executor:
                stream_results = list(
                    executor.map(
                        self._download,
                        [self.settings.download_bytes_per_stream]
                        * max(1, self.settings.download_streams),
                    )
                )
            downloaded = sum(item[0] for item in stream_results)
            download_seconds = max(item[2] for item in stream_results) - min(
                item[1] for item in stream_results
            )
            download_mbps = throughput_mbps(downloaded, download_seconds)

            uploaded, upload_seconds = self._upload(self.settings.upload_bytes)
            upload_mbps = throughput_mbps(uploaded, upload_seconds)
            return SpeedResult(
                tested_at=utc_now_iso(),
                download_mbps=round(download_mbps, 2),
                upload_mbps=round(upload_mbps, 2),
                duration_seconds=round(time.perf_counter() - started, 2),
                target=urllib.parse.urlsplit(self.settings.download_url).netloc,
            )
        except Exception as exc:
            return SpeedResult(
                tested_at=utc_now_iso(),
                download_mbps=None,
                upload_mbps=None,
                duration_seconds=round(time.perf_counter() - started, 2),
                target=urllib.parse.urlsplit(self.settings.download_url).netloc,
                error=f"{type(exc).__name__}: {exc}",
            )

    def _download(self, byte_count: int) -> tuple[int, float, float]:
        separator = "&" if "?" in self.settings.download_url else "?"
        url = f"{self.settings.download_url}{separator}bytes={byte_count}"
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept-Encoding": "identity",
                "Cache-Control": "no-cache",
            },
        )
        started = time.perf_counter()
        received = 0
        with urllib.request.urlopen(
            request, timeout=self.settings.timeout_seconds
        ) as response:
            while chunk := response.read(64 * 1024):
                received += len(chunk)
        finished = time.perf_counter()
        if received == 0:
            raise OSError("download endpoint returned no data")
        return received, started, finished

    def _upload(self, byte_count: int) -> tuple[int, float]:
        payload = b"\0" * byte_count
        request = urllib.request.Request(
            self.settings.upload_url,
            data=payload,
            method="POST",
            headers={
                "User-Agent": USER_AGENT,
                "Content-Type": "application/octet-stream",
                "Cache-Control": "no-cache",
            },
        )
        started = time.perf_counter()
        with urllib.request.urlopen(
            request, timeout=self.settings.timeout_seconds
        ) as response:
            response.read()
            if response.status >= 400:
                raise OSError(f"upload returned HTTP {response.status}")
        return byte_count, time.perf_counter() - started
