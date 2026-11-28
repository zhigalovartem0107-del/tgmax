"""Черновики и очередь Telegram-бота на постоянном диске."""

import json
import sqlite3
import time


class BotQueue:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS drafts (owner INTEGER PRIMARY KEY, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT, owner INTEGER NOT NULL,
                body TEXT NOT NULL, due INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'queued'
            );
            CREATE INDEX IF NOT EXISTS posts_due ON posts(status, due);
            CREATE TABLE IF NOT EXISTS source_groups (key TEXT PRIMARY KEY, post_id INTEGER NOT NULL);
        """)
        # Telegram мог принять запрос до остановки процесса. Не публикуем его повторно.
        with self.db:
            self.db.execute("UPDATE posts SET status='unknown', body='{}' WHERE status='sending'")
            self.db.execute("DELETE FROM source_groups WHERE post_id IN (SELECT id FROM posts WHERE status!='queued')")

    def close(self):
        self.db.close()

    def bind(self, bot_id):
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO state VALUES ('bot_id', ?)", (bot_id,))
            saved = self.db.execute("SELECT value FROM state WHERE key='bot_id'").fetchone()[0]
        if saved != bot_id:
            raise ValueError("Эта очередь принадлежит другому Telegram-боту. Задай отдельный DATA_DIR.")

    def offset(self):
        state = dict(self.db.execute("SELECT key, value FROM state WHERE key IN ('offset', 'newest')"))
        return state.get("offset", 0) if time.time() - state.get("newest", 0) < 6 * 86400 else 0

    def advance(self, update_id):
        self.db.executemany("INSERT INTO state VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                            (("offset", update_id + 1), ("newest", int(time.time()))))

    def draft(self, owner):
        row = self.db.execute("SELECT body FROM drafts WHERE owner=?", (owner,)).fetchone()
        return json.loads(row["body"]) if row else None

    def save(self, owner, draft):
        self.db.execute("INSERT INTO drafts VALUES (?, ?) ON CONFLICT(owner) DO UPDATE SET body=excluded.body",
                        (owner, json.dumps(draft, ensure_ascii=False)))

    def discard(self, owner):
        return self.db.execute("DELETE FROM drafts WHERE owner=?", (owner,)).rowcount > 0

    def enqueue(self, owner, draft, channel, now):
        draft = dict(draft, channel=channel)
        cur = self.db.execute("INSERT INTO posts(owner, body, due) VALUES (?, ?, ?)",
                              (owner, json.dumps(draft, ensure_ascii=False), now + draft["minutes"] * 60))
        self.discard(owner)
        return cur.lastrowid

    def relay(self, owner, source, message_id, group, channel, now):
        draft = {"channel": channel, "source": source, "ids": [message_id], "ad": False}
        due = now + 5 if group else now
        if group:
            key = f"{source}:{group}"
            row = self.db.execute("SELECT posts.* FROM posts JOIN source_groups ON posts.id=source_groups.post_id "
                                  "WHERE source_groups.key=?", (key,)).fetchone()
            if row and row["status"] == "queued":
                saved = json.loads(row["body"])
                saved["ids"] = sorted(set(saved["ids"] + [message_id]))
                self.db.execute("UPDATE posts SET body=?, due=? WHERE id=?",
                                (json.dumps(saved), due, row["id"]))
                return
        cur = self.db.execute("INSERT INTO posts(owner, body, due) VALUES (?, ?, ?)",
                              (owner, json.dumps(draft), due))
        if group:
            self.db.execute("INSERT INTO source_groups VALUES (?, ?) ON CONFLICT(key) "
                            "DO UPDATE SET post_id=excluded.post_id", (key, cur.lastrowid))

    def cancel(self, owner, post_id):
        canceled = self.db.execute("UPDATE posts SET status='canceled', body='{}' "
                                   "WHERE owner=? AND id=? AND status='queued'", (owner, post_id)).rowcount > 0
        if canceled:
            self.db.execute("DELETE FROM source_groups WHERE post_id=?", (post_id,))
        return canceled

    def recent(self, owner):
        return self.db.execute("SELECT id, due, status FROM posts WHERE owner=? ORDER BY id DESC LIMIT 10",
                               (owner,)).fetchall()

    def claim(self, now):
        with self.db:
            return self.db.execute("""
                UPDATE posts SET status='sending'
                WHERE id=(SELECT id FROM posts WHERE status='queued' AND due<=? ORDER BY due,id LIMIT 1)
                RETURNING *
            """, (now,)).fetchone()


    def result(self, post_id, status, due=None):
        with self.db:
            if due is None:
                self.db.execute("DELETE FROM source_groups WHERE post_id=?", (post_id,))
                self.db.execute("UPDATE posts SET status=?, body='{}' WHERE id=?", (status, post_id))
            else:
                self.db.execute("UPDATE posts SET status=?, due=? WHERE id=?", (status, due, post_id))
