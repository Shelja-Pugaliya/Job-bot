"""Module 6 (local part): SQLite log of every job, found -> tailored -> applied -> response.
Google Sheets sync comes later; this DB stays the source of truth for de-duplication."""
import sqlite3
from datetime import datetime

from .config import ROOT

DB_PATH = ROOT / "data" / "jobs.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY, source TEXT, company TEXT, title TEXT, location TEXT, url TEXT,
  posted TEXT, description TEXT, prescore INTEGER, senior INTEGER, track TEXT,
  match_score INTEGER, status TEXT, folder TEXT, found_at TEXT, updated_at TEXT, notes TEXT
)"""

STATUSES = ["found", "skipped", "tailored", "review", "applied", "rejected", "interview", "offer"]


def db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute(_SCHEMA)
    return con


def add_found(jobs: list[dict]) -> set[str]:
    """Insert jobs not seen before. Returns the ids that were new."""
    now = datetime.now().isoformat(timespec="seconds")
    with db() as con:
        seen = {r["id"] for r in con.execute("SELECT id FROM jobs")}
        new = [j for j in jobs if j["id"] not in seen]
        con.executemany(
            "INSERT INTO jobs (id, source, company, title, location, url, posted, description, prescore,"
            " senior, track, status, found_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?, 'found', ?, ?)",
            [(j["id"], j["source"], j["company"], j["title"], j["location"], j["url"], j["posted"],
              j["description"], j["prescore"], int(j["senior"]), j["track"], now, now) for j in new])
    return {j["id"] for j in new}


def queue(limit: int, min_prescore: int = 0) -> list[sqlite3.Row]:
    with db() as con:
        return con.execute(
            "SELECT * FROM jobs WHERE status='found' AND prescore>=? ORDER BY prescore DESC, posted DESC LIMIT ?",
            (min_prescore, limit)).fetchall()


def update(job_id: str, **fields) -> None:
    fields["updated_at"] = datetime.now().isoformat(timespec="seconds")
    cols = ", ".join(f"{k}=?" for k in fields)
    with db() as con:
        con.execute(f"UPDATE jobs SET {cols} WHERE id=?", (*fields.values(), job_id))


def counts() -> dict[str, int]:
    with db() as con:
        return {r["status"]: r["n"] for r in con.execute("SELECT status, COUNT(*) n FROM jobs GROUP BY status")}
