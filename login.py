# Вход в аккаунт Telegram. Открой этот файл и нажми запуск — бот спросит ключи и код, потом начнёт работу.
import os
import runpy
import sys

BOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tg_to_max.py")
sys.argv = [BOT, "--login"]
runpy.run_path(BOT, run_name="__main__")
