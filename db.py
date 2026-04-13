"""
SQLite database — Nikethan Reels Toolkit
"""
import sqlite3, os
from flask import g

BASE_DIR = os.path.dirname(__file__)
DB_PATH  = os.path.join(BASE_DIR, 'reels_db.sqlite')

SCHEMA = """
CREATE TABLE IF NOT EXISTS reels (
  id            TEXT PRIMARY KEY,
  url           TEXT NOT NULL,
  account       TEXT,
  title         TEXT,
  caption       TEXT,
  tags          TEXT DEFAULT '[]',
  mentions      TEXT DEFAULT '[]',
  duration      INTEGER,
  file_path     TEXT,
  thumbnail     TEXT,
  downloaded_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  watermarked   INTEGER DEFAULT 0,
  watermark_folder TEXT,
  status        TEXT DEFAULT 'pending',
  is_posted     INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS settings (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ig_extractions (
  id           TEXT PRIMARY KEY,
  username     TEXT NOT NULL,
  list_type    TEXT NOT NULL,
  count        INTEGER DEFAULT 0,
  data_path    TEXT,
  extracted_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ig_accounts (
  id            TEXT PRIMARY KEY,
  label         TEXT,
  priority      INTEGER DEFAULT 1,
  cookie_path   TEXT,
  is_active     INTEGER DEFAULT 1,
  error_count   INTEGER DEFAULT 0,
  last_used     DATETIME
);

CREATE TABLE IF NOT EXISTS channel_snapshots (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  channel_id    TEXT NOT NULL,
  channel_title TEXT,
  platform      TEXT DEFAULT 'youtube',
  slot          INTEGER DEFAULT 1,
  snapshot_json TEXT NOT NULL,
  captured_at   DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_snapshot_slot ON channel_snapshots(channel_id, slot);
"""

def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DB_PATH)
        db.row_factory = sqlite3.Row
    return db

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()
