"""Migration 003 must keep legacy data intact and insert valid seed rows.

Runs the real Alembic revisions against a throwaway SQLite database: build a
schema at revision 002, add legacy rows, then upgrade to 003.
"""
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

API_DIR = Path(__file__).resolve().parents[1]


def _alembic(db_path: Path, revision: str) -> None:
    env = {
        **os.environ,
        "ENV": "development",
        "DATABASE_URL": f"sqlite:///{db_path.as_posix()}",
        "USE_SQLITE": "true",
    }
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", revision],
        cwd=API_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr


def test_003_collapses_departments_keeps_completed_and_uses_true(tmp_path):
    db_path = tmp_path / "legacy.db"
    _alembic(db_path, "002")

    con = sqlite3.connect(db_path)
    try:
        for index, (name, department) in enumerate(
            [
                ("Dr A", "Cardiology"),
                ("Dr B", "Cardiology"),
                ("Dr C", "Nephrology"),
            ],
            start=1,
        ):
            con.execute(
                "INSERT INTO users "
                "(id, email, hashed_password, name, role, department, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
                (f"{index:032x}", f"doctor{index}@example.com", "x", name, "doctor", department),
            )
        con.execute(
            "INSERT INTO users "
            "(id, email, hashed_password, name, role, created_at) "
            "VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
            ("a" * 32, "patient@example.com", "x", "Pat", "patient"),
        )
        con.execute(
            "INSERT INTO patients (id, user_id, age, condition, risk_score, status) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("b" * 32, "a" * 32, 30, "x", 1, "Active"),
        )
        con.execute(
            "INSERT INTO appointments "
            "(id, patient_id, doctor_id, scheduled_at, department, status, mode) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "c" * 32,
                "b" * 32,
                "1" * 32,
                "2026-10-01 10:00:00+00:00",
                "Cardiology",
                "Completed",
                "In-person",
            ),
        )
        con.commit()
    finally:
        con.close()

    _alembic(db_path, "003")

    con = sqlite3.connect(db_path)
    try:
        specialties = con.execute(
            "SELECT name, COUNT(*) FROM specialties GROUP BY name"
        ).fetchall()
        assert specialties == [("Cardiology", 1), ("Nephrology", 1)]

        statuses = [row[0] for row in con.execute("SELECT status FROM appointments")]
        assert statuses == ["Completed"]

        active = [row[0] for row in con.execute("SELECT is_active FROM doctor_shifts")]
        assert active and all(bool(value) for value in active)
    finally:
        con.close()
