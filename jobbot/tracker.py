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

STATUSES = ["found", "ranked", "skipped", "tailored", "review", "applied", "rejected", "interview", "offer"]
_ADDED_COLUMNS = {"analysis": "TEXT", "dealbreaker": "TEXT", "years_required": "INTEGER"}


def db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute(_SCHEMA)
    have = {r["name"] for r in con.execute("PRAGMA table_info(jobs)")}
    for col, typ in _ADDED_COLUMNS.items():
        if col not in have:
            con.execute(f"ALTER TABLE jobs ADD COLUMN {col} {typ}")
    return con


def to_rank(limit: int) -> list[sqlite3.Row]:
    """Unanalyzed jobs, best pre-score first."""
    with db() as con:
        return con.execute("SELECT * FROM jobs WHERE status='found' ORDER BY prescore DESC, posted DESC LIMIT ?",
                           (limit,)).fetchall()


def best_ranked(limit: int) -> list[sqlite3.Row]:
    """Analyzed, not yet tailored, no dealbreaker: best real match score first."""
    with db() as con:
        return con.execute("SELECT * FROM jobs WHERE status='ranked' ORDER BY match_score DESC, posted DESC LIMIT ?",
                           (limit,)).fetchall()


def add_found(jobs: list[dict]) -> set[str]:
    """Insert jobs not seen before. Returns the ids that were new."""
    now = datetime.now().isoformat(timespec="seconds")
    with db() as con:
        seen = {r["id"] for r in con.execute("SELECT id FROM jobs")}
        new = [j for j in jobs if j["id"] not in seen]
        con.executemany(
            "INSERT INTO jobs (id, source, company, title, location, url, posted, description, prescore,"
            " senior, track, years_required, status, found_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?, 'found', ?, ?)",
            [(j["id"], j["source"], j["company"], j["title"], j["location"], j["url"], j["posted"],
              j["description"], j["prescore"], int(j["senior"]), j["track"], j["years_required"], now, now)
             for j in new])
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


def all_jobs(status: str | None = None) -> list[sqlite3.Row]:
    sql = "SELECT * FROM jobs" + (" WHERE status=?" if status else "") + \
          " ORDER BY COALESCE(match_score, prescore) DESC, posted DESC"
    with db() as con:
        return con.execute(sql, (status,) if status else ()).fetchall()


def counts() -> dict[str, int]:
    with db() as con:
        return {r["status"]: r["n"] for r in con.execute("SELECT status, COUNT(*) n FROM jobs GROUP BY status")}
