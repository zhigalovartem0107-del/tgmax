"""Файлы данных бота: чтение и атомарная запись JSON, поиск входов в Telegram на устройстве."""
import json
from contextlib import suppress
import os
import shutil
import threading
import time


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def load_json_safe(path, default):
    """Как load_json, но для важных файлов (настройки, кабинеты, темы): основной файл испорчен или пропал —
    берём резервную копию path + ".bak" (её пишет save_json(backup=True)). Испорченный файл — в .broken."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        if not os.path.exists(path + ".bak"):
            return default                              # первый запуск — копии ещё нет
    except Exception:
        with suppress(OSError):
            shutil.copyfile(path, path + ".broken")
    try:
        with open(path + ".bak", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return default
    print(f"💾 {path}: файл испорчен или пропал — поднял данные из резервной копии {path}.bak")
    return data


_WRITE_LOCK = threading.Lock()


def _write(path, data, private, sync):
    tmp = path + ".tmp"
    with suppress(FileNotFoundError):
        os.remove(tmp)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600 if private else 0o644)
    with open(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
        if sync:                                        # важное — на диск до замены: сбой не оставит пустой файл
            f.flush()
            os.fsync(f.fileno())
    os.replace(tmp, path)


def save_json(path, data, private=False, backup=False):
    """Атомарная запись: если процесс убили посередине, файл не повреждается.
    private — файл с ключами: читать и писать может только бот (права 600).
    backup — важный файл (настройки, кабинеты, темы): пишем на диск надёжно и держим копию path + ".bak"."""
    with _WRITE_LOCK:
        _write(path, data, private, sync=backup)
        if backup:
            _write(path + ".bak", data, private, sync=True)


DATA_FILES = ["settings.json", "themes.json", "queue.json", "pub_state.json", "last_posts.json",
              "posted_history.json", "stash.json", "cards.json", "channel_id.txt"]


DATA_GLOBS = ["queue_*.json", "pub_state_*.json", "stash_*.json", "last_posts_*.json", "posted_history_*.json"]


SKIP_DIRS = {"DCIM", "Pictures", "Movies", "Music", "Podcasts", "Telegram", "WhatsApp", "Alarms",
             "Ringtones", "Notifications", "Download.tmp", "cache", "__pycache__"}


def _find_sessions(roots, skip, max_depth=5, max_dirs=20000, max_sec=15):
    """Все найденные на устройстве файлы session.session (кроме папки skip), свежие первыми."""
    found, seen, t0, visited = {}, set(), time.time(), 0
    skip = os.path.abspath(skip)
    for root in roots:
        stack = [(root, 0)]
        while stack and visited < max_dirs and time.time() - t0 < max_sec:
            d, depth = stack.pop()
            real = os.path.realpath(d)
            if real in seen:
                continue
            seen.add(real)
            visited += 1
            try:
                entries = list(os.scandir(d))
            except OSError:
                continue
            for e in entries:
                try:
                    if e.is_file() and e.name == "session.session":
                        if os.path.abspath(d) != skip and os.path.getsize(e.path) > 0:
                            found[os.path.abspath(d)] = e.stat().st_mtime
                    elif (e.is_dir(follow_symlinks=False) and depth < max_depth
                          and not e.name.startswith(".") and e.name not in SKIP_DIRS):
                        stack.append((e.path, depth + 1))
                except OSError:
                    continue
    return [d for d, _ in sorted(found.items(), key=lambda kv: -kv[1])]
