"""Настройки из переменных окружения (без секретов: токены и ключи — в переменных хостинга, см. tg_to_max.py).

ADMIN_IDS            — MAX user_id администраторов бота через запятую: доступ к /admin и панели.
WELCOME_VIDEO_PATH   — файл приветственного видео (путь на сервере).
WELCOME_VIDEO_URL    — или ссылка на видео (скачивается один раз, если файла нет).
"""
import os


def env_text(*names, env=None):
    values = os.environ if env is None else env
    for name in names:
        value = (values.get(name) or "").strip()
        if value:
            return value
    return ""


def data_dir():
    return env_text("TG_MAX_DIR", "DATA_DIR")


def owner_config_in_env():
    return env_text("BOT_ENV_ONLY") == "1" and env_text("TGMAX_CLIENT") != "1"


def parse_ids(raw):
    """«12, 34;56» -> [12, 34, 56]. Мусор пропускаем."""
    out = []
    for part in str(raw or "").replace(";", ",").split(","):
        part = part.strip()
        if part.isdigit():
            out.append(int(part))
    return out


ADMIN_IDS = parse_ids(os.environ.get("ADMIN_IDS"))
WELCOME_VIDEO_PATH = (os.environ.get("WELCOME_VIDEO_PATH") or "").strip()
WELCOME_VIDEO_URL = (os.environ.get("WELCOME_VIDEO_URL") or "").strip()
