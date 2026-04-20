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

CREATE TABLE IF NOT EXISTS poster_accounts (
  id               INTEGER PRIMARY KEY,   -- slot 1-5
  label            TEXT DEFAULT '',        -- friendly name e.g. "Account 1"
  username         TEXT DEFAULT '',
  password         TEXT DEFAULT '',
  folder_path      TEXT DEFAULT '',        -- folder to pull videos from
  caption          TEXT DEFAULT '',        -- per-account caption template
  tags             TEXT DEFAULT '',        -- per-account hashtags
  max_posts_batch  INTEGER DEFAULT 10,     -- posts before cooling starts
  cool_minutes     INTEGER DEFAULT 120,    -- cooling period in minutes (2 hrs)
  interval_minutes INTEGER DEFAULT 15,     -- gap between individual posts (mins)
  session_ttl_hours INTEGER DEFAULT 24,    -- how long local IG session cookies stay valid
  session_established_at DATETIME,         -- when local session was last refreshed
  enabled          INTEGER DEFAULT 0,      -- 0=disabled, 1=enabled
  posts_in_window  INTEGER DEFAULT 0,      -- posts made in current window
  window_start     DATETIME,              -- when current window started
  last_posted_at   DATETIME,              -- last successful post time
  status           TEXT DEFAULT 'idle',   -- idle | posting | cooling | error
  note             TEXT DEFAULT ''        -- last status message
);

-- Seed slots 1–5 if they don't exist
INSERT OR IGNORE INTO poster_accounts (id, label) VALUES
  (1, 'Account 1'),
  (2, 'Account 2'),
  (3, 'Account 3'),
  (4, 'Account 4'),
  (5, 'Account 5');

CREATE TABLE IF NOT EXISTS poster_log (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id INTEGER,
  file_path  TEXT,
  outcome    TEXT,      -- 'posted' | 'skipped' | 'error'
  note       TEXT,
  logged_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS poster_connection_tests (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id       INTEGER NOT NULL,
  outcome          TEXT DEFAULT 'error',      -- success|invalid_credentials|ip_ban|challenge|error
  status_code      INTEGER,
  summary          TEXT DEFAULT '',
  raw_response     TEXT DEFAULT '',
  is_ip_ban        INTEGER DEFAULT 0,
  is_rate_limited  INTEGER DEFAULT 0,
  tested_at        DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_conn_tests_account_time
  ON poster_connection_tests(account_id, tested_at DESC);

CREATE TABLE IF NOT EXISTS post_queue (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  platform       TEXT NOT NULL,    -- 'youtube' | 'instagram'
  account_slot   INTEGER,          -- instagram: poster_accounts.id (1-5); youtube: always 1
  file_path      TEXT NOT NULL,
  title          TEXT DEFAULT '',
  description    TEXT DEFAULT '',
  tags           TEXT DEFAULT '',  -- comma-separated or JSON array
  privacy        TEXT DEFAULT 'public',   -- youtube: public/unlisted/private
  category_id    TEXT DEFAULT '22',       -- youtube category (22=People&Blogs)
  thumbnail_path TEXT DEFAULT '',
  scheduled_at   DATETIME,         -- NULL = post as soon as possible
  status         TEXT DEFAULT 'pending',  -- pending|posting|done|error|cancelled
  ai_generated   INTEGER DEFAULT 0,       -- 1 if content was AI-generated
  posted_at      DATETIME,
  note           TEXT DEFAULT '',
  created_at     DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_queue_status ON post_queue(status, scheduled_at);

CREATE TABLE IF NOT EXISTS youtube_accounts (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  label            TEXT NOT NULL,
  cookie_path      TEXT,
  usage_count      INTEGER DEFAULT 0,
  max_usage        INTEGER DEFAULT 20,
  is_active        INTEGER DEFAULT 1,
  status           TEXT DEFAULT 'ok',
  first_error_at   DATETIME,
  last_used_at     DATETIME,
  created_at       DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Seed slots 1-10 if they don't exist
INSERT OR IGNORE INTO youtube_accounts (id, label) VALUES
  (1, 'Account 1'), (2, 'Account 2'), (3, 'Account 3'), (4, 'Account 4'), (5, 'Account 5'),
  (6, 'Account 6'), (7, 'Account 7'), (8, 'Account 8'), (9, 'Account 9'), (10, 'Account 10');
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
    # Lightweight schema migrations for existing installs.
    
    # 1. Migrate poster_accounts
    cols_pa = {r[1] for r in conn.execute("PRAGMA table_info('poster_accounts')").fetchall()}
    if 'session_ttl_hours' not in cols_pa:
        conn.execute('ALTER TABLE poster_accounts ADD COLUMN session_ttl_hours INTEGER DEFAULT 24')
    if 'session_established_at' not in cols_pa:
        conn.execute('ALTER TABLE poster_accounts ADD COLUMN session_established_at DATETIME')
        
    # 2. Migrate reels table (Fixes "no column named status" error)
    cols_reels = {r[1] for r in conn.execute("PRAGMA table_info('reels')").fetchall()}
    if 'status' not in cols_reels:
        conn.execute("ALTER TABLE reels ADD COLUMN status TEXT DEFAULT 'pending'")
    if 'watermarked' not in cols_reels:
        conn.execute("ALTER TABLE reels ADD COLUMN watermarked INTEGER DEFAULT 0")
    if 'watermark_folder' not in cols_reels:
        conn.execute("ALTER TABLE reels ADD COLUMN watermark_folder TEXT")
    if 'is_posted' not in cols_reels:
        conn.execute("ALTER TABLE reels ADD COLUMN is_posted INTEGER DEFAULT 0")
        
    conn.commit()
    conn.close()
