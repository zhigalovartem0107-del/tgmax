"""Карточка покупателя: собирает данные из записи кабинета, тем и журнала оплат. Пустое поле — «—», не падаем."""

DASH = "—"


def _get(value, default=DASH):
    return value if value not in (None, "", [], {}) else default


def _first(mapping):
    return next((v for v in (mapping or {}).values() if v), "")


def build_profile(key, rec, themes, pay_log, now, check_min, fmt_date):
    """themes: [{"name", "chat_id", "link", "sources", "published", "last_pub"}] — по темам кабинета."""
    rec = rec or {}
    key = str(key or "")
    limits = {"channels": _get(rec.get("lim_themes")), "sources": _get(rec.get("lim_sources")),
              "check_min": _get(check_min)}
    if DASH in (limits["channels"], limits["sources"]):
        limits = {k: DASH for k in limits}
    mx = [{"title": t["name"], "link": _get(t.get("link"))} for t in themes if t.get("chat_id")]
    payments = []
    for p in sorted((p for p in pay_log if str(p.get("key")) == key), key=lambda p: p.get("t", 0)):
        payments.append({"date": fmt_date(p["t"]) if p.get("t") else DASH, "amount": _get(p.get("price"), 0),
                         "provider": _get(p.get("how")), "tariff": _get(p.get("plan")),
                         "code": _get(p.get("code"))})
    last = max([t.get("last_pub") or 0 for t in themes] + [p.get("t") or 0 for p in pay_log
                                                          if str(p.get("key")) == key] + [0])
    until = _get(rec.get("until") or None)
    return {
        "id": _get(key),
        "username": _get(_first(rec.get("usernames")) or rec.get("username")),
        "name": _get(_first(rec.get("names")) or rec.get("name")),
        "created": fmt_date(rec["created"]) if rec.get("created") else DASH,
        "plan": _get(rec.get("plan")),
        "until": until,
        "until_text": fmt_date(until) if until != DASH else DASH,
        "limits": limits,
        "mx_channels": mx,
        "theme_names": [t["name"] for t in themes],
        "tg_sources": {t["name"]: list(t.get("sources") or []) for t in themes},
        "published": sum(int(t.get("published") or 0) for t in themes),
        "last_activity": last or DASH,
        "last_activity_text": fmt_date(last) if last else DASH,
        "payments": payments,
    }


def contact_info(profile):
    """Связь лично — из аккаунта продавца в MAX. Бот сам первым писать покупателю не может."""
    user = profile["username"]
    if user != DASH:
        url = f"https://max.ru/{user}"
        return f"✉️ Личный чат: @{user} — {url}\nНапиши от своего аккаунта MAX: бот первым писать не может.", url
    return (f"✉️ Личный чат: нет @username. Найди покупателя в MAX по id {profile['id']} "
            "и напиши от своего аккаунта."), None


def render_profile(p, admin=True):
    lim = p["limits"]
    lines = ["👤 Профиль покупателя" if admin else "👤 Мой профиль", ""]
    if admin:
        lines += [f"ID: {p['id']}", f"@username: {'@' + p['username'] if p['username'] != DASH else DASH}"]
    lines += [f"Имя: {p['name']}", f"В боте с: {p['created']}", "",
              f"💳 Тариф: {p['plan']}", f"Срок до: {p['until_text']}",
              f"Лимиты: каналов MAX — {lim['channels']}, источников в теме — {lim['sources']}, "
              f"проверка каждые {lim['check_min']} мин", ""]
    lines.append("📺 Каналы MAX: " + ("" if p["mx_channels"] else DASH))
    lines += [f"• {c['title']} — {c['link']}" for c in p["mx_channels"]]
    lines.append("📡 Источники Telegram:" + ("" if p["tg_sources"] else f" {DASH}"))
    for name, srcs in p["tg_sources"].items():
        lines.append(f"• {name}: " + (", ".join(srcs) if srcs else DASH))
    lines += ["", f"🗂 Тем: {len(p['theme_names'])}" + (f" ({', '.join(p['theme_names'])})" if p["theme_names"] else ""),
              f"📊 Опубликовано постов: {p['published']} · последняя активность: {p['last_activity_text']}", "",
              "🧾 Оплаты:"]
    if not p["payments"]:
        lines.append(DASH)
    for pay in p["payments"]:
        lines.append(f"• {pay['date']} · {pay['amount']} ₽ · {pay['provider']} · {pay['tariff']} · код: {pay['code']}")
    if admin:
        lines += ["", contact_info(p)[0]]
    return "\n".join(lines)


def match_buyer(key, rec, query):
    """Поиск по id или @username (и по имени). Пустой запрос — всё подходит."""
    q = str(query or "").strip().lstrip("@").lower()
    if not q:
        return True
    if q == str(key).lower():
        return True
    rec = rec or {}
    values = [rec.get("name"), *(rec.get("names") or {}).values(), *(rec.get("usernames") or {}).values()]
    return any(q in str(v).lower() for v in values if v)


def paginate(items, page, size=8):
    pages = max(1, -(-len(items) // size))
    page = max(0, min(int(page), pages - 1))
    return items[page * size:(page + 1) * size], page, pages
