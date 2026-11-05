"""tools/generate_promo.py: 10-секундное промо — телефон слева, текст справа, сценарий «один клик»."""
import importlib.util
import os
import shutil
import subprocess
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("generate_promo", os.path.join(ROOT, "tools", "generate_promo.py"))
gp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gp)

assert gp.DURATION == 10.0 and (gp.W, gp.H) == (1280, 720)

# 1) сценарий по порядку: пост → кнопка → клик → тост → канал MAX → логотип
assert gp.CLICK_AT < gp.TOAST_AT < gp.CHANNEL_AT < gp.LOGO_AT <= 8.1
assert gp.CURSOR_IN < gp.CLICK_AT and gp.BUTTON_IN <= gp.CURSOR_IN
assert gp.LOGO_AT < gp.DURATION

# 2) появление: уведомление сверху, карточка снизу, кнопка «Опубликовать» есть до клика
assert gp.notify_dy(gp.NOTIFY_IN - 0.1) < 0 and gp.notify_dy(gp.NOTIFY_IN + 0.8) == 0
assert gp.card_y(gp.BUTTON_IN + 0.5) == gp.CARD_Y and gp.card_y(0.0) > gp.CARD_Y

# 3) клик: курсор доходит до кнопки и нажимает; до клика кнопка обычного цвета
bx, by = gp.button_center()
x_end, y_end = gp.cursor_pos(gp.CLICK_AT)
assert abs(x_end - bx) < 2 and abs(y_end - by) < 2, (x_end, y_end, bx, by)
assert gp.button_color(gp.CLICK_AT - 0.2) == gp.ACCENT
assert gp.button_color(gp.CLICK_AT + 0.1) != gp.ACCENT

# 4) подписи справа: «Один клик» видно в момент клика, а не раньше
assert gp.caption_alpha("Один клик", gp.CLICK_AT) > 0.95
assert gp.caption_alpha("Один клик", 1.0) == 0.0
for t in [i / 20 for i in range(0, 201)]:
    for c in gp.CAPTIONS:
        assert 0.0 <= gp.caption_alpha(c[2], t) <= 1.0

# 5) переход: в момент 6.0–6.8 экран переезжает к каналу; дальше — только канал
assert gp.transition_k(5.0) == 0.0 and gp.transition_k(gp.CHANNEL_AT + 1.0) == 1.0
assert 0.0 < gp.transition_k((gp.CHANNEL_AT + gp.CHANNEL_AT + 0.8) / 2) < 1.0

# 6) телефон уходит, логотип приходит в конце
assert gp.phone_alpha(2.0) == 1.0 and gp.phone_alpha(9.5) == 0.0
assert gp.logo_alpha(9.0) == 1.0 and gp.logo_alpha(4.0) == 0.0

# 7) кадры: нужный размер; до и после перехода — разные
f_chat = gp.make_frame(3.0)
f_chan = gp.make_frame(7.5)
assert f_chat.shape == (720, 1280, 3) and f_chat.dtype.name == "uint8"
assert (f_chat.astype(int) != f_chan.astype(int)).any()
print("generate_promo: сценарий, кнопка, переход, логотип — ok")

# 8) рендер: 10 с, если есть moviepy и ffmpeg (короткий прогон: 4 fps)
have_ffmpeg = shutil.which("ffmpeg") and shutil.which("ffprobe")
try:
    import moviepy  # noqa: F401
    have_moviepy = True
except ImportError:
    have_moviepy = False
if have_moviepy and have_ffmpeg:
    out = os.path.join(tempfile.mkdtemp(), "promo.mp4")
    gp.render(out, fps=4)
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                "-of", "default=nw=1:nk=1", out], capture_output=True, text=True).stdout.strip())
    assert abs(dur - 10.0) < 0.3, dur
    print("generate_promo: рендер mp4 — ok, длительность", round(dur, 2), "с")
else:
    print("generate_promo: рендер пропущен (нужны moviepy и ffmpeg)")
print("ВСЕ ТЕСТЫ GENERATE_PROMO ПРОШЛИ")
