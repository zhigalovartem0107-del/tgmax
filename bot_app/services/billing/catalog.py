"""Каталог тарифов: строится из действующего списка тарифов (один источник правды), лимиты — по-русски в боте."""

PAID_CHECK_MIN = 1        # платные: проверка каждую минуту, как обычная скорость бота
DURATION_DAYS = 30


def catalog(tariffs, free_check_min=15, paid_check_min=PAID_CHECK_MIN):
    return [{"id": t["name"], "title": t["name"], "price_rub": int(t["price"]), "duration_days": DURATION_DAYS,
             "max_channels": int(t["themes"]), "max_sources": int(t["sources"]),
             "check_interval_min": paid_check_min} for t in tariffs]


def find(entries, tariff_id):
    return next((e for e in entries if e["id"] == tariff_id), None)


def over_limit(themes_count, sources_per_theme, tariff):
    """Предупреждения, если уже больше лимитов тарифа. Ничего не удаляем — только новые добавить нельзя."""
    warn = []
    if themes_count > tariff["max_channels"]:
        warn.append(f"каналов MAX: {themes_count}, в тарифе {tariff['max_channels']}")
    for name, n in sources_per_theme:
        if n > tariff["max_sources"]:
            warn.append(f"в теме «{name}» источников: {n}, в тарифе до {tariff['max_sources']}")
    return warn
