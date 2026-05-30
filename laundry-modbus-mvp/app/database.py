from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/laundry.db")


def utc_now() -> str:
	return datetime.now(UTC).isoformat()


def db_path() -> Path:
	if DATABASE_URL.startswith("sqlite:///"):
		path = Path(DATABASE_URL.removeprefix("sqlite:///"))
		if not path.is_absolute():
			path = Path.cwd() / path
		path.parent.mkdir(parents=True, exist_ok=True)
		return path
	raise RuntimeError("MVP currently supports SQLite DATABASE_URL values only")


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
	conn = sqlite3.connect(db_path())
	conn.row_factory = sqlite3.Row
	conn.execute("PRAGMA foreign_keys = ON")
	try:
		yield conn
		conn.commit()
	finally:
		conn.close()


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
	if row is None:
		return None
	return {key: row[key] for key in row.keys()}


def rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
	return [row_to_dict(row) or {} for row in rows]


def init_db() -> None:
	with connect() as conn:
		conn.executescript(
			"""
			CREATE TABLE IF NOT EXISTS machines (
				id TEXT PRIMARY KEY,
				name TEXT NOT NULL,
				slave_address INTEGER NOT NULL UNIQUE,
				type TEXT NOT NULL CHECK(type IN ('washer', 'dryer')),
				capacity_kg INTEGER NOT NULL,
				default_program INTEGER NOT NULL DEFAULT 1,
				enabled INTEGER NOT NULL DEFAULT 1,
				created_at TEXT NOT NULL,
				updated_at TEXT NOT NULL
			);

			CREATE TABLE IF NOT EXISTS machine_status (
				machine_id TEXT PRIMARY KEY REFERENCES machines(id) ON DELETE CASCADE,
				status TEXT NOT NULL DEFAULT 'offline',
				program_number INTEGER,
				remaining_minutes INTEGER NOT NULL DEFAULT 0,
				error_code INTEGER NOT NULL DEFAULT 0,
				error_message TEXT,
				door_state TEXT NOT NULL DEFAULT 'unknown',
				last_seen TEXT,
				raw_registers TEXT NOT NULL DEFAULT '{}',
				updated_at TEXT NOT NULL
			);

			CREATE TABLE IF NOT EXISTS programs (
				id INTEGER PRIMARY KEY AUTOINCREMENT,
				machine_type TEXT NOT NULL,
				program_number INTEGER NOT NULL,
				name TEXT NOT NULL,
				duration_minutes INTEGER NOT NULL,
				UNIQUE(machine_type, program_number)
			);

			CREATE TABLE IF NOT EXISTS prices (
				id INTEGER PRIMARY KEY AUTOINCREMENT,
				machine_id TEXT NOT NULL REFERENCES machines(id) ON DELETE CASCADE,
				program_number INTEGER NOT NULL,
				price_cents INTEGER NOT NULL,
				currency TEXT NOT NULL DEFAULT 'EUR',
				updated_at TEXT NOT NULL,
				UNIQUE(machine_id, program_number)
			);

			CREATE TABLE IF NOT EXISTS transactions (
				id TEXT PRIMARY KEY,
				machine_id TEXT NOT NULL REFERENCES machines(id),
				program_number INTEGER NOT NULL,
				amount_cents INTEGER NOT NULL,
				currency TEXT NOT NULL,
				status TEXT NOT NULL,
				payment_reference TEXT UNIQUE,
				created_at TEXT NOT NULL,
				updated_at TEXT NOT NULL
			);

			CREATE TABLE IF NOT EXISTS events (
				id INTEGER PRIMARY KEY AUTOINCREMENT,
				machine_id TEXT REFERENCES machines(id),
				level TEXT NOT NULL,
				event_type TEXT NOT NULL,
				message TEXT NOT NULL,
				payload TEXT NOT NULL DEFAULT '{}',
				created_at TEXT NOT NULL
			);

			CREATE TABLE IF NOT EXISTS errors (
				id INTEGER PRIMARY KEY AUTOINCREMENT,
				machine_id TEXT REFERENCES machines(id),
				error_code INTEGER NOT NULL,
				message TEXT NOT NULL,
				active INTEGER NOT NULL DEFAULT 1,
				created_at TEXT NOT NULL,
				cleared_at TEXT
			);

			CREATE TABLE IF NOT EXISTS settings (
				key TEXT PRIMARY KEY,
				value TEXT NOT NULL,
				updated_at TEXT NOT NULL
			);
			"""
		)


def seed_defaults() -> None:
	now = utc_now()
	machines = [
		("W01", "Washer 01", 1, "washer", 8, 1),
		("W02", "Washer 02", 2, "washer", 12, 1),
		("D01", "Dryer 01", 3, "dryer", 14, 1),
	]
	programs = [
		("washer", 1, "Cotton 40", 45),
		("washer", 2, "Quick wash", 25),
		("dryer", 1, "Normal dry", 40),
		("dryer", 2, "Delicate dry", 30),
	]
	with connect() as conn:
		for program in programs:
			conn.execute(
				"INSERT OR IGNORE INTO programs(machine_type, program_number, name, duration_minutes) VALUES (?, ?, ?, ?)",
				program,
			)
		for machine in machines:
			conn.execute(
				"""
				INSERT OR IGNORE INTO machines(id, name, slave_address, type, capacity_kg, default_program, created_at, updated_at)
				VALUES (?, ?, ?, ?, ?, ?, ?, ?)
				""",
				(*machine, now, now),
			)
			conn.execute(
				"INSERT OR IGNORE INTO machine_status(machine_id, updated_at) VALUES (?, ?)",
				(machine[0], now),
			)
			conn.execute(
				"INSERT OR IGNORE INTO prices(machine_id, program_number, price_cents, currency, updated_at) VALUES (?, ?, ?, ?, ?)",
				(machine[0], machine[5], 500, "EUR", now),
			)


def log_event(machine_id: str | None, level: str, event_type: str, message: str, payload: dict[str, Any] | None = None) -> None:
	with connect() as conn:
		conn.execute(
			"INSERT INTO events(machine_id, level, event_type, message, payload, created_at) VALUES (?, ?, ?, ?, ?, ?)",
			(machine_id, level, event_type, message, json.dumps(payload or {}), utc_now()),
		)
