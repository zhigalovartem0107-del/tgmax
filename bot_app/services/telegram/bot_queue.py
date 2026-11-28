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
            CREATE TABLE IF NOT EXISTS max_inbox (id INTEGER PRIMARY KEY AUTOINCREMENT, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS max_state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS max_sources (
                id INTEGER PRIMARY KEY, link TEXT NOT NULL, title TEXT NOT NULL, since INTEGER
            );
            CREATE TABLE IF NOT EXISTS max_pending (link TEXT PRIMARY KEY);
            CREATE INDEX IF NOT EXISTS max_source_link ON max_sources(link);
            CREATE TABLE IF NOT EXISTS max_seen (
                source INTEGER NOT NULL, mid TEXT NOT NULL, timestamp INTEGER NOT NULL, PRIMARY KEY(source, mid)
            );
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

    def relay_target(self):
        row = self.db.execute("SELECT value FROM state WHERE key='relay_target'").fetchone()
        return row[0] if row else None

    def set_relay_target(self, cid):
        self.db.execute("INSERT OR REPLACE INTO state VALUES ('relay_target', ?)", (cid,))

    def max_marker(self):
        row = self.db.execute("SELECT value FROM max_state WHERE key='marker'").fetchone()
        return json.loads(row[0]) if row else None

    def set_max_marker(self, marker):
        self.db.execute("INSERT OR REPLACE INTO max_state VALUES ('marker', ?)", (json.dumps(marker),))

    def max_inbox(self):
        return self.db.execute("SELECT id, body FROM max_inbox ORDER BY id LIMIT 100").fetchall()

    def ack_max(self, event_id):
        self.db.execute("DELETE FROM max_inbox WHERE id=?", (event_id,))

    def max_sources(self):
        return self.db.execute("SELECT * FROM max_sources ORDER BY since IS NULL, title").fetchall()

    def max_source_by_link(self, link):
        return self.db.execute("SELECT * FROM max_sources WHERE link=? LIMIT 1", (link,)).fetchone()

    def max_pending(self):
        return [row[0] for row in self.db.execute("SELECT link FROM max_pending ORDER BY link")]

    def set_max_pending(self, link):
        self.db.execute("INSERT OR IGNORE INTO max_pending VALUES (?)", (link,))

    def drop_max_pending(self, link):
        return bool(self.db.execute("DELETE FROM max_pending WHERE link=?", (link,)).rowcount)

    def remember_max(self, cid, link, title):
        self.db.execute("INSERT INTO max_sources(id, link, title) VALUES (?, ?, ?) "
                        "ON CONFLICT(id) DO UPDATE SET link=excluded.link, title=excluded.title", (cid, link, title))

    def enable_max(self, cid, since):
        self.db.execute("UPDATE max_sources SET since=COALESCE(since, ?) WHERE id=?", (since, cid))
        self.db.execute("DELETE FROM max_pending WHERE link=(SELECT link FROM max_sources WHERE id=?)", (cid,))

    def disable_max(self, cid):
        changed = self.db.execute("UPDATE max_sources SET since=NULL WHERE id=? AND since IS NOT NULL", (cid,)).rowcount
        if changed:
            self.db.execute("UPDATE posts SET status='canceled', body='{}' "
                            "WHERE status='queued' AND json_extract(body, '$.max_source')=?", (cid,))
        return bool(changed)

    def enqueue_max(self, owner, target, source, mid, timestamp, now, urls):
        row = self.db.execute("SELECT since FROM max_sources WHERE id=?", (source,)).fetchone()
        if not row or row["since"] is None or timestamp <= row["since"]:
            return
        new = self.db.execute("INSERT OR IGNORE INTO max_seen VALUES (?, ?, ?)", (source, mid, timestamp)).rowcount
        if not new:
            return
        direct = [url if isinstance(url, str) and len(url) <= 4096 else None for url in urls]
        draft = {"max_mid": mid, "max_source": source, "channel": target, "max_urls": direct}
        self.db.execute("INSERT INTO posts(owner, body, due) VALUES (?, ?, ?)",
                        (owner, json.dumps(draft), now))
        self.db.execute("DELETE FROM max_seen WHERE timestamp<?", ((now - 7 * 86400) * 1000,))

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
