"""tools/generate_promo.py — вертикальный промо-ролик 1080x1920, 10 с, чёрный фон, фиолетовый и розовый акценты."""
import importlib.util
import os
import shutil
import subprocess
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("generate_promo", os.path.join(ROOT, "tools", "generate_promo.py"))
gp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gp)

# 1) формат: вертикальный телефонный, 10 с; цвета — чёрный, фиолетовый #8A2BE2, розовый #FF1493
assert (gp.W, gp.H) == (1080, 1920) and gp.DURATION == 10.0
assert gp.BLACK == (0, 0, 0) and gp.PURPLE == (138, 43, 226) and gp.PINK == (255, 20, 147)

# 2) сцены по порядку
assert gp.phone_y(0.0) == gp.H and gp.phone_y(0.9) == gp.PHONE_REST_Y      # 0–0.9: телефон въезжает снизу
assert gp.phone_y(9.0) == gp.H                                              # в конце телефон ушёл
assert gp.notif_y(1.0) is None and gp.notif_y(2.0) == 16 and gp.notif_y(3.0) is None  # уведомление 1.5–2.75
assert gp.card_visible(1.7) is False and gp.card_visible(1.8) is True
assert gp.button_pressed(3.4) is False and gp.button_pressed(3.6) is True and gp.button_pressed(4.0) is False
assert gp.swipe_k(4.4) == 0.0 and gp.swipe_k(5.0) == 1.0 and 0.0 < gp.swipe_k(4.67) < 1.0

# 3) курсор доходит до кнопки ровно к клику (3.5 с)
bx, by = gp.button_center()
cx, cy = gp.cursor_pos(gp.CLICK_AT)
assert abs(cx - bx) < 1 and abs(cy - by) < 1, (cx, cy, bx, by)

# 4) тексты по времени: «АВТОПОСТИНГ В MAX» — в начале, «1 КЛИК», «ГОТОВО», маркированные пункты, логотип
def words(t):
    return [w for w, *_ in gp.caption_items(t)]
assert "АВТОПОСТИНГ" in words(1.0) and "В MAX" in words(1.0)
assert "1 КЛИК" in words(4.0) and "ГОТОВО" not in words(4.0)
assert "ГОТОВО" in words(5.0) and "1 КЛИК" not in words(5.0)
assert words(6.1) == ["• Без задержек"] and "• Любые файлы" in words(6.8) and "• Аналитика" in words(7.5)
assert "Reloader" in words(9.0) and "Твой контент-завод" in words(9.0)

# 5) текст помещается по ширине 1080 (на телефоне читается, не обрезается)
for t in (1.0, 4.0, 5.0, 6.8, 9.0):
    for text, size, *_ in gp.caption_items(t):
        assert gp.text_width(text, size) <= gp.W - 2 * 40, (text, size, gp.text_width(text, size))

# 6) кадр: вертикальный, в кадре есть розовый текст и фиолетовый телефон
f = gp.make_frame(1.0)
assert f.shape == (1920, 1080, 3) and f.dtype.name == "uint8"
assert ((f[..., 0] > 200) & (f[..., 1] < 60) & (f[..., 2] > 120)).any(), "розовый текст"
assert f[0, 0].tolist() == [0, 0, 0], "фон — чёрный"
f2 = gp.make_frame(5.0)
assert (f.astype(int) != f2.astype(int)).any()
print("generate_promo: формат, сцены, курсор, тексты, кадр — ok")

# 7) рендер: если есть moviepy и ffmpeg — вертикальный mp4 10 с
have = shutil.which("ffmpeg") and shutil.which("ffprobe")
try:
    importlib.import_module("moviepy")
    have_moviepy = True
except ImportError:
    have_moviepy = False
if have and have_moviepy:
    out = os.path.join(tempfile.mkdtemp(), "promo.mp4")
    gp.render(out, fps=4)
    lines = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=width,height:format=duration", "-of", "csv=p=0", out],
                           capture_output=True, text=True).stdout.split()
    w, h = lines[0].split(",")[:2]
    assert (w, h) == ("1080", "1920"), lines
    assert abs(float(lines[-1]) - 10.0) < 0.3, lines
    print("generate_promo: рендер mp4 — ok, 1080x1920, 10 с")
else:
    print("generate_promo: рендер пропущен (нужны moviepy и ffmpeg)")
print("ВСЕ ТЕСТЫ GENERATE_PROMO ПРОШЛИ")
