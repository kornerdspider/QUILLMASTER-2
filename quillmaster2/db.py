"""SQLite storage for books, scenes and the Story Bible.

One file: <data folder>/quillmaster2.db
"""
import json
import sqlite3
import threading
import time
from contextlib import contextmanager

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS series (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, created REAL
);
CREATE TABLE IF NOT EXISTS books (
    id INTEGER PRIMARY KEY, series_id INTEGER NOT NULL REFERENCES series(id),
    title TEXT NOT NULL, position INTEGER NOT NULL DEFAULT 1,
    filename TEXT, word_count INTEGER DEFAULT 0,
    status TEXT DEFAULT 'imported',      -- imported | reading | read | planned
    created REAL
);
CREATE TABLE IF NOT EXISTS chapters (
    id INTEGER PRIMARY KEY, book_id INTEGER NOT NULL REFERENCES books(id),
    position INTEGER NOT NULL, title TEXT, word_count INTEGER DEFAULT 0,
    summary TEXT
);
CREATE TABLE IF NOT EXISTS scenes (
    id INTEGER PRIMARY KEY, book_id INTEGER NOT NULL REFERENCES books(id),
    chapter_id INTEGER NOT NULL REFERENCES chapters(id),
    position INTEGER NOT NULL, text TEXT NOT NULL, word_count INTEGER,
    fingerprint TEXT NOT NULL, read_fingerprint TEXT,
    summary TEXT, pov TEXT, location TEXT, time_text TEXT, read_at REAL
);
CREATE TABLE IF NOT EXISTS entities (
    id INTEGER PRIMARY KEY, series_id INTEGER NOT NULL REFERENCES series(id),
    type TEXT NOT NULL,                  -- character | place | object | rule | note
    name TEXT NOT NULL, aliases TEXT DEFAULT '[]', created REAL
);
CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY, entity_id INTEGER NOT NULL REFERENCES entities(id),
    field TEXT NOT NULL, value TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'found', -- canon | found | conflict | retired | planned
    confidence REAL DEFAULT 0.7, book_id INTEGER, scene_id INTEGER,
    created REAL, updated REAL
);
CREATE TABLE IF NOT EXISTS fact_sources (
    id INTEGER PRIMARY KEY, fact_id INTEGER NOT NULL REFERENCES facts(id),
    kind TEXT NOT NULL,                  -- text | qa | hand
    scene_id INTEGER, quote TEXT, question_id INTEGER, created REAL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY, series_id INTEGER, book_id INTEGER, scene_id INTEGER,
    description TEXT, when_text TEXT, story_order INTEGER
);
CREATE TABLE IF NOT EXISTS threads (
    id INTEGER PRIMARY KEY, series_id INTEGER NOT NULL, name TEXT NOT NULL,
    status TEXT DEFAULT 'open',          -- open | resolved | dropped | planned
    note TEXT
);
CREATE TABLE IF NOT EXISTS thread_beats (
    id INTEGER PRIMARY KEY, thread_id INTEGER NOT NULL REFERENCES threads(id),
    scene_id INTEGER NOT NULL, book_id INTEGER, chapter_id INTEGER,
    role TEXT,                           -- opens | rises | turns | resolves | mentions
    note TEXT
);
CREATE TABLE IF NOT EXISTS questions (
    id INTEGER PRIMARY KEY, series_id INTEGER NOT NULL,
    kind TEXT NOT NULL,                  -- conflict | same_entity | thread | gap
    text TEXT NOT NULL, priority INTEGER DEFAULT 3,
    status TEXT DEFAULT 'open',          -- open | answered | skipped | intentional
    answer TEXT, payload TEXT DEFAULT '{}', created REAL, answered_at REAL,
    dedupe_key TEXT UNIQUE
);
CREATE TABLE IF NOT EXISTS appearances (
    scene_id INTEGER NOT NULL, entity_id INTEGER NOT NULL,
    PRIMARY KEY (scene_id, entity_id)
);
CREATE TABLE IF NOT EXISTS fixes (
    id INTEGER PRIMARY KEY, series_id INTEGER, scene_id INTEGER, quote TEXT,
    note TEXT, status TEXT DEFAULT 'open', question_id INTEGER, created REAL
);
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY, series_id INTEGER, book_id INTEGER,
    kind TEXT NOT NULL, content TEXT, built_at REAL
);
CREATE TABLE IF NOT EXISTS findings (
    id INTEGER PRIMARY KEY, book_id INTEGER NOT NULL, chapter_id INTEGER, scene_id INTEGER,
    pass TEXT NOT NULL, agent TEXT, kind TEXT,
    severity TEXT DEFAULT 'medium',      -- high | medium | low
    start INTEGER, "end" INTEGER, quote TEXT, message TEXT NOT NULL, suggestion TEXT,
    confidence REAL DEFAULT 0.7, fact_ids TEXT DEFAULT '[]',
    status TEXT DEFAULT 'open',          -- open | accepted | rejected | stale
    run_id INTEGER, created REAL
);
CREATE TABLE IF NOT EXISTS pass_runs (
    id INTEGER PRIMARY KEY, book_id INTEGER, pass TEXT, chapter_ids TEXT,
    started REAL, finished REAL, found INTEGER DEFAULT 0, note TEXT
);
CREATE INDEX IF NOT EXISTS ix_findings_scene ON findings(scene_id, status);
CREATE INDEX IF NOT EXISTS ix_findings_book ON findings(book_id, pass, status);
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY, kind TEXT, target_id INTEGER,
    status TEXT DEFAULT 'queued',        -- queued | running | paused | done | error
    progress INTEGER DEFAULT 0, total INTEGER DEFAULT 0, message TEXT,
    started REAL, finished REAL
);
CREATE INDEX IF NOT EXISTS ix_scenes_book ON scenes(book_id, position);
CREATE INDEX IF NOT EXISTS ix_facts_entity ON facts(entity_id, field);
CREATE INDEX IF NOT EXISTS ix_sources_fact ON fact_sources(fact_id);
CREATE INDEX IF NOT EXISTS ix_sources_scene ON fact_sources(scene_id);
CREATE INDEX IF NOT EXISTS ix_beats_thread ON thread_beats(thread_id);
"""

_lock = threading.RLock()
_db_path_override = None


def set_path(path):
    """Used by tests to point at a temporary database."""
    global _db_path_override
    _db_path_override = str(path)


def db_path():
    return _db_path_override or str(config.data_dir() / "quillmaster2.db")


def connect():
    conn = sqlite3.connect(db_path(), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def tx():
    """One locked transaction. Commits on success, rolls back on error."""
    with _lock:
        conn = connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


# Columns added after build 1. Older databases get them on start.
MIGRATIONS = [
    ("scenes", "beat", "TEXT"), ("scenes", "moves_plot", "INTEGER"),
    ("scenes", "goal", "TEXT"), ("scenes", "conflict", "TEXT"),
    ("scenes", "outcome", "TEXT"), ("scenes", "tension", "INTEGER"),
    ("books", "last_backup", "REAL"), ("books", "edited", "REAL"),
]


def init():
    with tx() as c:
        c.executescript(SCHEMA)
        for table, col, typ in MIGRATIONS:
            cols = {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
            if col not in cols:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")


def now():
    return time.time()


def rows(cursor):
    return [dict(r) for r in cursor.fetchall()]


def row(cursor):
    r = cursor.fetchone()
    return dict(r) if r else None


def jload(text, default=None):
    try:
        return json.loads(text) if text else default
    except ValueError:
        return default
