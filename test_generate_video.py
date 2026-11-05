"""tools/generate_video.py: 10-секундное промо-видео. Чистые функции — всегда; рендер — если есть moviepy и ffmpeg."""
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("generate_video", os.path.join(ROOT, "tools", "generate_video.py"))
gv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gv)

assert gv.DURATION == 10.0
assert gv.TITLE == "Reloader - Auto-posting to MAX"

# 1) прозрачность текста: от 0 до 1 и обратно, без скачков за границами
for t in [i / 10 for i in range(0, 101)]:
    a = gv.text_alpha(t)
    assert 0.0 <= a <= 1.0, (t, a)
assert gv.text_alpha(0.0) == 0.0 and gv.text_alpha(2.0) == 1.0 and gv.text_alpha(10.0) == 0.0

# 2) полёт поста: до старта — у Telegram, после конца — у MAX; путь монотонный по x
x0, _ = gv.post_position(0.0, 1280, 720)
x1, _ = gv.post_position(gv.FLIGHT_START, 1280, 720)
x2, _ = gv.post_position(gv.FLIGHT_END, 1280, 720)
x3, _ = gv.post_position(9.0, 1280, 720)
assert x0 == x1 and x2 == x3 and x0 < x2, (x0, x1, x2, x3)
xs = [gv.post_position(gv.FLIGHT_START + (gv.FLIGHT_END - gv.FLIGHT_START) * i / 50, 1280, 720)[0] for i in range(51)]
assert all(a <= b for a, b in zip(xs, xs[1:])), "пост летит вперёд, а не назад"
_, ymid = gv.post_position((gv.FLIGHT_START + gv.FLIGHT_END) / 2, 1280, 720)
_, yedge = gv.post_position(gv.FLIGHT_START, 1280, 720)
assert ymid < yedge, "по дуге: в середине полёта выше, чем на старте"

# 3) кадр: нужный размер и тип
frame = gv.make_frame(4.0, 320, 180)
assert frame.shape == (180, 320, 3) and frame.dtype.name == "uint8", (frame.shape, frame.dtype)
print("generate_video: кадры и тайминг — ok")

# 4) рендер короткого ролика: длительность ~10 с (если есть moviepy и ffmpeg)
have_ffmpeg = shutil.which("ffmpeg") and shutil.which("ffprobe")
try:
    import moviepy  # noqa: F401
    have_moviepy = True
except ImportError:
    have_moviepy = False
if have_moviepy and have_ffmpeg:
    out = os.path.join(tempfile.mkdtemp(), "welcome.mp4")
    gv.render(out, width=320, height=180, fps=8)
    assert os.path.getsize(out) > 1000, "файл пустой"
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                "-of", "default=nw=1:nk=1", out], capture_output=True, text=True).stdout.strip())
    assert abs(dur - 10.0) < 0.2, dur
    print("generate_video: рендер mp4 — ok, длительность", round(dur, 2), "с")
else:
    print("generate_video: рендер пропущен (нужны moviepy и ffmpeg)")
print("ВСЕ ТЕСТЫ GENERATE_VIDEO ПРОШЛИ")
