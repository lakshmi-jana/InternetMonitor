from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Iterator

from .models import ConnectivityResult, SpeedResult


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS connectivity (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    checked_at TEXT NOT NULL,
                    online INTEGER NOT NULL,
                    latency_ms REAL,
                    jitter_ms REAL,
                    failed_probes INTEGER NOT NULL,
                    dns_ms REAL,
                    error TEXT
                );
                CREATE INDEX IF NOT EXISTS ix_connectivity_checked_at
                    ON connectivity(checked_at);

                CREATE TABLE IF NOT EXISTS speed_tests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tested_at TEXT NOT NULL,
                    download_mbps REAL,
                    upload_mbps REAL,
                    duration_seconds REAL NOT NULL,
                    target TEXT NOT NULL,
                    error TEXT
                );
                CREATE INDEX IF NOT EXISTS ix_speed_tests_tested_at
                    ON speed_tests(tested_at);
                """
            )
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(connectivity)")
            }
            for name, column_type in (
                ("packet_loss_percent", "REAL"),
                ("packets_sent", "INTEGER"),
                ("packets_lost", "INTEGER"),
            ):
                if name not in columns:
                    connection.execute(
                        f"ALTER TABLE connectivity ADD COLUMN {name} {column_type}"
                    )

    def add_connectivity(self, result: ConnectivityResult) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO connectivity (
                    checked_at, online, latency_ms, jitter_ms, failed_probes, dns_ms, error,
                    packet_loss_percent, packets_sent, packets_lost
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result.checked_at,
                    int(result.online),
                    result.latency_ms,
                    result.jitter_ms,
                    result.failed_probes,
                    result.dns_ms,
                    result.error,
                    result.packet_loss_percent,
                    result.packets_sent,
                    result.packets_lost,
                ),
            )

    def add_speed_test(self, result: SpeedResult) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO speed_tests
                    (tested_at, download_mbps, upload_mbps, duration_seconds, target, error)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    result.tested_at,
                    result.download_mbps,
                    result.upload_mbps,
                    result.duration_seconds,
                    result.target,
                    result.error,
                ),
            )

    def latest_connectivity(self) -> dict | None:
        return self._one(
            """
            SELECT checked_at, online, latency_ms, jitter_ms, failed_probes, dns_ms, error,
                   packet_loss_percent, packets_sent, packets_lost
            FROM connectivity ORDER BY checked_at DESC LIMIT 1
            """,
            boolean_fields={"online"},
        )

    def latest_speed_test(self) -> dict | None:
        return self._one(
            """
            SELECT tested_at, download_mbps, upload_mbps, duration_seconds, target, error
            FROM speed_tests ORDER BY tested_at DESC LIMIT 1
            """
        )

    def logs(
        self,
        hours: int = 24 * 90,
        failures_only: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> dict:
        hours = max(1, min(hours, 24 * 90))
        limit = max(1, min(limit, 500))
        offset = max(0, offset)
        since = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()
        failure_clause = "WHERE failed = 1" if failures_only else ""
        source = f"""
            SELECT checked_at AS logged_at,
                   'connectivity' AS kind,
                   CASE
                       WHEN online = 0 OR error IS NOT NULL OR failed_probes > 0
                            OR COALESCE(packet_loss_percent, 0) > 0
                       THEN 1 ELSE 0
                   END AS failed,
                   CASE WHEN online = 1 THEN 'Online' ELSE 'Offline' END AS summary,
                   error,
                   latency_ms,
                   jitter_ms,
                   failed_probes,
                   NULL AS download_mbps,
                   NULL AS upload_mbps,
                   NULL AS target,
                   packet_loss_percent
            FROM connectivity
            WHERE checked_at >= ?
            UNION ALL
            SELECT tested_at,
                   'speed',
                   CASE WHEN error IS NOT NULL THEN 1 ELSE 0 END,
                   target,
                   error,
                   NULL,
                   NULL,
                   NULL,
                   download_mbps,
                   upload_mbps,
                   target,
                   NULL
            FROM speed_tests
            WHERE tested_at >= ?
        """
        parameters = (since, since)
        total_rows = self._many(
            f"SELECT COUNT(*) AS total FROM ({source}) {failure_clause}",
            parameters,
        )
        rows = self._many(
            f"""
            SELECT logged_at, kind, failed, summary, error, latency_ms, jitter_ms,
                   failed_probes, download_mbps, upload_mbps, target, packet_loss_percent
            FROM ({source})
            {failure_clause}
            ORDER BY logged_at DESC
            LIMIT ? OFFSET ?
            """,
            (*parameters, limit, offset),
            {"failed"},
        )
        return {
            "items": rows,
            "total": int(total_rows[0]["total"]) if total_rows else 0,
            "limit": limit,
            "offset": offset,
        }

    def history(self, hours: int = 24) -> dict[str, list[dict]]:
        hours = max(1, min(hours, 24 * 90))
        since = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()
        return {
            "connectivity": self._many(
                """
                SELECT checked_at, online, latency_ms, jitter_ms, failed_probes, dns_ms, error,
                       packet_loss_percent, packets_sent, packets_lost
                FROM connectivity WHERE checked_at >= ? ORDER BY checked_at
                """,
                (since,),
                {"online"},
            ),
            "speed_tests": self._many(
                """
                SELECT tested_at, download_mbps, upload_mbps, duration_seconds, target, error
                FROM speed_tests WHERE tested_at >= ? ORDER BY tested_at
                """,
                (since,),
            ),
        }

    def cleanup(self, retention_days: int) -> int:
        cutoff = (datetime.now(UTC) - timedelta(days=max(1, retention_days))).isoformat()
        with self._connection() as connection:
            first = connection.execute(
                "DELETE FROM connectivity WHERE checked_at < ?", (cutoff,)
            ).rowcount
            second = connection.execute(
                "DELETE FROM speed_tests WHERE tested_at < ?", (cutoff,)
            ).rowcount
        return first + second

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _one(self, query: str, boolean_fields: set[str] | None = None) -> dict | None:
        rows = self._many(query, boolean_fields=boolean_fields)
        return rows[0] if rows else None

    def _many(
        self,
        query: str,
        parameters: tuple = (),
        boolean_fields: set[str] | None = None,
    ) -> list[dict]:
        with self._connection() as connection:
            rows = [dict(row) for row in connection.execute(query, parameters).fetchall()]
        for row in rows:
            for field in boolean_fields or ():
                row[field] = bool(row[field])
        return rows
