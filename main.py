import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("TG_MAX_DIR") or os.path.join(HERE, "data")
os.makedirs(DATA, exist_ok=True)
os.chdir(DATA)
os.environ.setdefault("TG_MAX_NO_SEARCH", "1")
BOT = os.path.join(HERE, "tg_to_max.py")
sys.argv = [BOT] + sys.argv[1:]
runpy.run_path(BOT, run_name="__main__")
