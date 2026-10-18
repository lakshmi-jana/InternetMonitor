import threading
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from internet_monitor.config import ConnectivitySettings, SpeedSettings
from internet_monitor.probes import (
    ConnectivityProbe,
    HttpSpeedTest,
    parse_ping_statistics,
    throughput_mbps,
)


class FakeSpeedHandler(BaseHTTPRequestHandler):
    uploaded_bytes = 0

    def do_GET(self) -> None:
        route = urllib.parse.urlsplit(self.path)
        if route.path == "/probe":
            self.send_response(204)
            self.end_headers()
            return
        if route.path == "/missing":
            self.send_response(404)
            self.end_headers()
            return
        byte_count = int(urllib.parse.parse_qs(route.query)["bytes"][0])
        body = b"x" * byte_count
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        byte_count = int(self.headers["Content-Length"])
        FakeSpeedHandler.uploaded_bytes = len(self.rfile.read(byte_count))
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, format: str, *args) -> None:
        return


class ProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeSpeedHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def test_throughput_calculation_uses_decimal_megabits(self) -> None:
        self.assertEqual(throughput_mbps(1_000_000, 1), 8)
        with self.assertRaises(ValueError):
            throughput_mbps(1, 0)

    def test_http_speed_test_uses_configured_endpoints(self) -> None:
        settings = SpeedSettings(
            download_url=f"{self.base_url}/download",
            upload_url=f"{self.base_url}/upload",
            download_bytes_per_stream=8_192,
            download_streams=2,
            upload_bytes=4_096,
            timeout_seconds=2,
        )

        result = HttpSpeedTest(settings).run()

        self.assertIsNone(result.error)
        self.assertGreater(result.download_mbps, 0)
        self.assertGreater(result.upload_mbps, 0)
        self.assertEqual(FakeSpeedHandler.uploaded_bytes, 4_096)

    def test_connectivity_probe_reports_local_endpoint_online(self) -> None:
        settings = ConnectivitySettings(
            interval_seconds=30,
            dns_host="localhost",
            probe_url=f"{self.base_url}/probe",
            attempts=2,
            timeout_seconds=2,
        )

        result = ConnectivityProbe(
            settings, ping_probe=lambda host, count, timeout_ms: (10, 1, 10.0, "Ping: lost 1 of 10")
        ).run()

        self.assertTrue(result.online)
        self.assertEqual(result.failed_probes, 0)
        self.assertEqual(result.packet_loss_percent, 10.0)
        self.assertEqual(result.packets_lost, 1)
        self.assertIn("Ping: lost 1 of 10", result.error)

    def test_ping_statistics_parser(self) -> None:
        output = """
        Ping statistics for 1.1.1.1:
            Packets: Sent = 10, Received = 8, Lost = 2 (20% loss),
        """
        self.assertEqual(parse_ping_statistics(output), (10, 2, 20.0))
        self.assertIsNone(parse_ping_statistics("Ping request could not find host"))

    def test_http_404_still_counts_as_reachable(self) -> None:
        settings = ConnectivitySettings(
            dns_host="localhost",
            probe_url=f"{self.base_url}/missing",
            attempts=1,
            timeout_seconds=2,
        )

        result = ConnectivityProbe(
            settings, ping_probe=lambda host, count, timeout_ms: (1, 0, 0.0, None)
        ).run()

        self.assertTrue(result.online)
        self.assertIsNone(result.error)


if __name__ == "__main__":
    unittest.main()
