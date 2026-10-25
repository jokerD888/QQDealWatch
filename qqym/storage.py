import sqlite3
import time
from pathlib import Path


class Storage:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS messages (
                group_id INTEGER NOT NULL, message_id TEXT NOT NULL,
                received_at INTEGER NOT NULL, PRIMARY KEY (group_id, message_id)
            );
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dedupe_key TEXT NOT NULL, group_id INTEGER NOT NULL,
                title TEXT NOT NULL, content TEXT NOT NULL,
                voice_requested INTEGER NOT NULL, created_at INTEGER NOT NULL,
                app_sent_at INTEGER, voice_attempted_at INTEGER,
                app_attempts INTEGER NOT NULL DEFAULT 0,
                app_retry_at INTEGER NOT NULL DEFAULT 0,
                app_receipt_code TEXT,
                last_error TEXT
            );
            CREATE INDEX IF NOT EXISTS alerts_dedupe ON alerts(dedupe_key, created_at);
            CREATE TABLE IF NOT EXISTS failed_events (
                group_id INTEGER NOT NULL, message_id TEXT NOT NULL,
                payload TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                retry_at INTEGER NOT NULL, last_error TEXT,
                PRIMARY KEY (group_id, message_id)
            );
            CREATE TABLE IF NOT EXISTS context_messages (
                group_id INTEGER NOT NULL, message_id TEXT NOT NULL,
                user_id INTEGER NOT NULL, text TEXT NOT NULL,
                received_at INTEGER NOT NULL,
                PRIMARY KEY (group_id, message_id)
            );
            CREATE INDEX IF NOT EXISTS context_lookup ON context_messages(group_id, user_id, received_at);
        """)
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(alerts)")}
        if "app_retry_at" not in columns:
            self.db.execute("ALTER TABLE alerts ADD COLUMN app_retry_at INTEGER NOT NULL DEFAULT 0")
        if "app_receipt_code" not in columns:
            self.db.execute("ALTER TABLE alerts ADD COLUMN app_receipt_code TEXT")
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def first_seen(self, group_id: int, message_id: str) -> bool:
        cursor = self.db.execute("INSERT OR IGNORE INTO messages VALUES (?, ?, ?)",
                                 (group_id, message_id, int(time.time())))
        self.db.commit()
        return cursor.rowcount == 1

    def remember_context(self, group_id: int, message_id: str, user_id: int, text: str) -> None:
        self.db.execute("INSERT OR IGNORE INTO context_messages VALUES (?, ?, ?, ?, ?)",
                        (group_id, message_id, user_id, text[:4000], int(time.time())))
        self.db.commit()

    def recent_context(self, group_id: int, message_id: str, user_id: int,
                       seconds: int = 120) -> list[str]:
        rows = self.db.execute("SELECT text FROM context_messages WHERE group_id=? AND "
                               "user_id=? AND message_id<>? AND received_at>=? "
                               "ORDER BY received_at DESC, rowid DESC LIMIT 2",
                               (group_id, user_id, message_id, int(time.time()) - seconds)).fetchall()
        return [row[0] for row in reversed(rows) if row[0]]

    def queue_alert(self, key: str, group_id: int, title: str, content: str,
                    voice: bool, dedupe_seconds: int) -> bool:
        now = int(time.time())
        exists = self.db.execute(
            "SELECT 1 FROM alerts WHERE dedupe_key=? AND created_at>? LIMIT 1",
            (key, now - dedupe_seconds)).fetchone()
        if exists:
            return False
        self.db.execute("INSERT INTO alerts (dedupe_key, group_id, title, content, voice_requested, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?)", (key, group_id, title, content, int(voice), now))
        self.db.commit()
        return True

    def pending_apps(self) -> list[tuple]:
        return self.db.execute("SELECT id, title, content FROM alerts WHERE app_sent_at IS NULL "
                               "AND app_retry_at<=? ORDER BY id LIMIT 50", (int(time.time()),)).fetchall()

    def app_result(self, alert_id: int, error: str | None = None,
                   receipt_code: str | None = None) -> None:
        now = int(time.time())
        attempts = self.db.execute("SELECT app_attempts FROM alerts WHERE id=?", (alert_id,)).fetchone()[0]
        retry_at = now + min(3600, 30 * 2 ** min(attempts, 7)) if error else 0
        self.db.execute("UPDATE alerts SET app_attempts=app_attempts+1, app_retry_at=?, "
                        "app_sent_at=CASE WHEN ? IS NULL THEN ? ELSE app_sent_at END, "
                        "app_receipt_code=COALESCE(?, app_receipt_code), last_error=? WHERE id=?",
                        (retry_at, error, now, receipt_code, error, alert_id))
        self.db.commit()

    def pending_voices(self) -> list[tuple]:
        return self.db.execute("SELECT id, title, content FROM alerts WHERE voice_requested=1 "
                               "AND app_sent_at IS NOT NULL AND voice_attempted_at IS NULL ORDER BY id LIMIT 20").fetchall()

    def recent_voice_count(self, since: int) -> int:
        return self.db.execute("SELECT COUNT(*) FROM alerts WHERE voice_attempted_at>=?", (since,)).fetchone()[0]

    def latest_voice(self) -> int | None:
        row = self.db.execute("SELECT MAX(voice_attempted_at) FROM alerts").fetchone()
        return row[0]

    def mark_voice_attempted(self, alert_id: int) -> None:
        # 发起请求前记录。网络超时可能已经扣费，故不自动重试电话。
        self.db.execute("UPDATE alerts SET voice_attempted_at=? WHERE id=?",
                        (int(time.time()), alert_id))
        self.db.commit()

    def skip_voice(self, alert_id: int) -> None:
        self.db.execute("UPDATE alerts SET voice_requested=0 WHERE id=?", (alert_id,))
        self.db.commit()

    def failed_event(self, group_id: int, message_id: str, payload: str, error: str) -> None:
        now = int(time.time())
        self.db.execute("INSERT INTO failed_events VALUES (?, ?, ?, 1, ?, ?) "
                        "ON CONFLICT(group_id,message_id) DO UPDATE SET "
                        "attempts=attempts+1, retry_at=?, last_error=?",
                        (group_id, message_id, payload, now + 60, error[:300], now + 60, error[:300]))
        self.db.commit()

    def retry_events(self) -> list[tuple]:
        return self.db.execute("SELECT group_id, message_id, payload, attempts FROM failed_events "
                               "WHERE retry_at<=? AND attempts<6 ORDER BY retry_at LIMIT 10",
                               (int(time.time()),)).fetchall()

    def retry_failed(self, group_id: int, message_id: str, error: str, attempts: int) -> None:
        delay = min(3600, 60 * 2 ** attempts)
        self.db.execute("UPDATE failed_events SET attempts=attempts+1, retry_at=?, last_error=? "
                        "WHERE group_id=? AND message_id=?",
                        (int(time.time()) + delay, error[:300], group_id, message_id))
        self.db.commit()

    def clear_failed(self, group_id: int, message_id: str) -> None:
        self.db.execute("DELETE FROM failed_events WHERE group_id=? AND message_id=?", (group_id, message_id))
        self.db.commit()

    def prune(self) -> None:
        cutoff = int(time.time()) - 30 * 86400
        self.db.execute("DELETE FROM messages WHERE received_at<?", (cutoff,))
        self.db.execute("DELETE FROM context_messages WHERE received_at<?", (int(time.time()) - 3600,))
        self.db.execute("DELETE FROM alerts WHERE created_at<? AND app_sent_at IS NOT NULL", (cutoff,))
        self.db.execute("DELETE FROM failed_events WHERE retry_at<?", (cutoff,))
        self.db.commit()
