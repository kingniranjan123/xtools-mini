"""
Analytics Snapshot Manager — GDG Circular Buffer
- Own channel: 7 slots (shift down, overwrite slot 7 = latest)
- External channels: 1 slot (always overwrite)
Uses direct sqlite3 (thread-safe, not Flask g).
"""
import sqlite3
import json
import os
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
DB_PATH = os.path.join(BASE_DIR, 'reels_db.sqlite')

MAX_SLOTS_OWN = 7
MAX_SLOTS_EXT = 1


def _con():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def save_snapshot(channel_id: str, channel_title: str, data: dict,
                  is_own_channel: bool = False, platform: str = 'youtube') -> int:
    """
    GDG-style circular buffer save.
    - Own channel: keeps 7 slots. Slot 1=oldest, slot 7=latest.
      On each call: delete slot 1 if count==7, shift remaining down, write slot 7.
    - External channel: always write/overwrite slot 1.
    Returns the slot number written.
    """
    max_slots = MAX_SLOTS_OWN if is_own_channel else MAX_SLOTS_EXT
    con = _con()
    try:
        # Get current slots for this channel
        rows = con.execute(
            'SELECT slot FROM channel_snapshots WHERE channel_id=? AND platform=? ORDER BY slot ASC',
            (channel_id, platform)
        ).fetchall()
        current_slots = [r['slot'] for r in rows]

        if is_own_channel:
            if len(current_slots) >= max_slots:
                # Shift: delete slot 1, renumber slot N -> slot N-1
                con.execute(
                    'DELETE FROM channel_snapshots WHERE channel_id=? AND platform=? AND slot=1',
                    (channel_id, platform)
                )
                con.execute(
                    'UPDATE channel_snapshots SET slot = slot - 1 WHERE channel_id=? AND platform=?',
                    (channel_id, platform)
                )
            # Write the new latest into the highest slot (max_slots or current count + 1)
            new_slot = min(len(current_slots) + 1, max_slots)
        else:
            new_slot = 1

        con.execute(
            '''INSERT INTO channel_snapshots
               (channel_id, channel_title, platform, slot, snapshot_json, captured_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(channel_id, slot) DO UPDATE SET
                 snapshot_json=excluded.snapshot_json,
                 captured_at=excluded.captured_at,
                 channel_title=excluded.channel_title
            ''',
            (channel_id, channel_title, platform, new_slot,
             json.dumps(data), datetime.utcnow().isoformat())
        )
        con.commit()
        return new_slot
    finally:
        con.close()


def get_snapshots(channel_id: str, platform: str = 'youtube') -> list:
    """Return all stored snapshots for a channel, latest first."""
    con = _con()
    try:
        rows = con.execute(
            '''SELECT slot, channel_title, captured_at, snapshot_json
               FROM channel_snapshots
               WHERE channel_id=? AND platform=?
               ORDER BY slot DESC''',
            (channel_id, platform)
        ).fetchall()
        result = []
        for r in rows:
            try:
                data = json.loads(r['snapshot_json'])
            except Exception:
                data = {}
            result.append({
                'slot': r['slot'],
                'channel_title': r['channel_title'],
                'captured_at': r['captured_at'],
                'data': data
            })
        return result
    finally:
        con.close()


def get_all_stored_channels(platform: str = 'youtube') -> list:
    """Return a list of all channels that have at least one snapshot."""
    con = _con()
    try:
        rows = con.execute(
            '''SELECT DISTINCT channel_id, channel_title,
                      MAX(captured_at) as last_captured,
                      COUNT(*) as slot_count
               FROM channel_snapshots WHERE platform=?
               GROUP BY channel_id ORDER BY last_captured DESC''',
            (platform,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()
