"""Время и подписи: склонения, минуты, даты, числа."""
import re
import time


def n_word(n, one, few, many):
    n = abs(n) % 100
    if 10 < n < 20:
        return many
    return one if n % 10 == 1 else few if 2 <= n % 10 <= 4 else many


def fmt_min(m):
    m = int(m)
    if m < 60:
        return f"{m} мин"
    return f"{m // 60} ч" + (f" {m % 60} мин" if m % 60 else "")


def fmt_date(ts):
    return time.strftime("%d.%m.%Y", time.localtime(ts))


def fmt_words(words):
    return ", ".join(words) if words else "—"


def plain_snip(text, n):
    """Начало текста для списков: без значков разметки (**, __) и переносов."""
    return re.sub(r"[*_~`]{2,}", "", text or "(без текста)").strip()[:n].replace("\n", " ")


def price90(p):
    """Цена за 90 дней: как 2,5 месяца (300 -> 750)."""
    return int(round(p * 2.5 / 10.0) * 10)


def num_sp(n):
    """12345 -> «12 345»."""
    return f"{int(round(n)):,}".replace(",", " ")


def week_range(since, now):
    a, b = time.localtime(since), time.localtime(now)
    if a.tm_mon == b.tm_mon:
        return f"{a.tm_mday}–{b.tm_mday} {MONTHS_GEN[b.tm_mon - 1]}"
    return f"{a.tm_mday} {MONTHS_SHORT[a.tm_mon - 1]} – {b.tm_mday} {MONTHS_SHORT[b.tm_mon - 1]}"


MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября",
              "ноября", "декабря"]


MONTHS_SHORT = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]
