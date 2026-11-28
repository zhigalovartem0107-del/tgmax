"""🤖 Вывод ИИ по цифрам канала — 4-я строка сообщения после картинки аналитики.

ИИ получает три строки (📈 / 👥 / 👁) и пояснение, что значат цифры, и отвечает 1–2 предложениями по-русски:
растёт ли канал, высокая ли вовлечённость. Ответ чистим: одна строка, без разметки и списков, не больше двух
предложений и 300 знаков. Только текст — без сети и без состояния бота."""
import re

PROMPT = ("Based on these stats, write a 1-2 sentence conclusion in Russian about the channel's health. "
          "Is it growing? Is engagement high? Use only the numbers below; «—» means the number is unknown — do not "
          "invent it. Answer with the conclusion only: plain text, no markdown, no lists, no greeting, "
          "at most 250 characters.\n\nStats:\n")
NOTES = ("Пояснения: «сегодня» и «за 7 дней» — прирост подписчиков; «Просмотры на пост» — средние просмотры поста "
         "за первые 24 / 48 / 72 часа; ER24 — просмотры поста за сутки к числу подписчиков.")
LIMIT = 300
_BULLET = re.compile(r"^([-*•·–]|\d+[.)])\s+")
_LABEL = re.compile(r"^\s*(вывод|итог|итоги|заключение|резюме|conclusion|summary)\s*[:—–-]\s*", re.I)


def ask_text(caption, d30=None):
    """Что отдаём ИИ: три строки с цифрами и пояснение (за 30 дней — если знаем)."""
    extra = f"\nЗа 30 дней: {d30}." if d30 else ""
    return f"{caption}{extra}\n\n{NOTES}"


def clean(text):
    """Ответ ИИ → одна строка: без разметки, списков и «Вывод:», не больше двух предложений и 300 знаков.
    Пусто — None."""
    if not isinstance(text, str):
        return None
    lines = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            if lines:
                break                                  # пустая строка после ответа — дальше лишнее
            continue
        if _BULLET.match(line):
            if lines:
                break                                  # список после ответа — лишнее
            line = _BULLET.sub("", line)
        lines.append(line)
    out = re.sub(r"[*_`#>]+", "", " ".join(lines))
    out = " ".join(_LABEL.sub("", out).split())
    out = " ".join(re.split(r"(?<=[.!?…])\s+", out)[:2]).strip()
    if len(out) > LIMIT:
        out = out[:LIMIT - 1].rsplit(" ", 1)[0].rstrip(",;:—– ") + "…"
    return out or None
