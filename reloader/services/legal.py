"""⚖️ Юридическое: реквизиты продавца, ссылки на документы, проверка ИНН/ОГРН, тексты для покупателя.

Без состояния бота — только данные и проверки (бот подставляет настройки из панели и переменных хостинга).
Законы, под которые это сделано: 152-ФЗ (персональные данные: кто оператор, что и зачем хранится, право на
удаление), 54-ФЗ (чек на почту), закон «О защите прав потребителей» (оферта, условия возврата, реквизиты
продавца), закон «О рекламе» (отписка). Это не юридическая консультация: тексты документов продавец пишет сам
(или с юристом), бот показывает его ссылки и честно говорит, что хранит."""
import hashlib
import json
import re

FORMS = ("ИП", "ООО", "Самозанятый")
FIELDS = {   # ключ: (название, вид, переменная хостинга)
    "form": ("Форма", "form", "LEGAL_FORM"),
    "name": ("Наименование", "text", "LEGAL_NAME"),
    "inn": ("ИНН", "inn", "LEGAL_INN"),
    "ogrn": ("ОГРН / ОГРНИП", "ogrn", "LEGAL_OGRN"),
    "address": ("Юридический адрес", "text", "LEGAL_ADDRESS"),
    "email": ("Почта поддержки", "email", "SUPPORT_EMAIL"),
    "phone": ("Телефон поддержки", "phone", "SUPPORT_PHONE"),
    "offer_url": ("Оферта", "url", "OFFER_URL"),
    "privacy_url": ("Политика конфиденциальности", "url", "PRIVACY_URL"),
    "refund_url": ("Условия возврата", "url", "REFUND_URL"),
    "cookies_url": ("Политика cookie", "url", "COOKIES_URL"),
}
DOCS = (("offer_url", "Оферта"), ("privacy_url", "Политика конфиденциальности"), ("refund_url", "Условия возврата"),
        ("cookies_url", "Политика cookie"))
DOCS_VER = 1           # встроенные тексты бота (что хранит бот, условия) поменялись — подними: согласие заново
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}$")


# ---------- контрольные цифры ----------
def inn_ok(inn):
    """ИНН: 10 цифр (организация) или 12 (ИП, самозанятый, физлицо) с верными контрольными цифрами."""
    s = str(inn or "")
    if not s.isdigit() or len(s) not in (10, 12):
        return False
    d = [int(c) for c in s]

    def check(coefs, digits):
        return sum(a * b for a, b in zip(coefs, digits)) % 11 % 10

    if len(d) == 10:
        return check([2, 4, 10, 3, 5, 9, 4, 6, 8], d) == d[9]
    return (check([7, 2, 4, 10, 3, 5, 9, 4, 6, 8], d) == d[10]
            and check([3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8], d) == d[11])


def ogrn_ok(ogrn):
    """ОГРН (13 цифр, организация) или ОГРНИП (15 цифр, ИП) с верной контрольной цифрой."""
    s = str(ogrn or "")
    if not s.isdigit() or len(s) not in (13, 15):
        return False
    mod = 11 if len(s) == 13 else 13
    return int(s[:-1]) % mod % 10 == int(s[-1])


# ---------- проверка ввода ----------
def validate(field, value, form=""):
    """(значение для сохранения, None) или (None, что не так). «-» — очистить поле."""
    v = " ".join(str(value or "").split())
    kind = FIELDS.get(field, ("", "text", ""))[1]
    if v == "-":
        return "", None
    if not v:
        return None, "Пусто. Пришли значение или «-», чтобы очистить."
    if kind == "form":
        for f in FORMS:
            if v.lower() in (f.lower(), "нпд" if f == "Самозанятый" else f.lower()):
                return f, None
        return None, "Форма — одна из: " + ", ".join(FORMS)
    if kind == "text":
        lo, hi = (2, 200) if field == "name" else (5, 300)
        if not lo <= len(v) <= hi:
            return None, f"Нужно от {lo} до {hi} символов."
        return v, None
    if kind == "inn":
        s = re.sub(r"[\s-]", "", v)
        if not s.isdigit():
            return None, "ИНН — только цифры."
        need = 10 if form == "ООО" else 12 if form in ("ИП", "Самозанятый") else None
        if need and len(s) != need:
            who = {"ООО": "ООО", "ИП": "ИП", "Самозанятый": "самозанятого"}[form]
            return None, f"У {who} ИНН — {need} цифр, а пришло {len(s)}."
        if len(s) not in (10, 12):
            return None, "ИНН — 10 цифр (организация) или 12 (ИП, самозанятый)."
        if not inn_ok(s):
            return None, "Не сходятся контрольные цифры ИНН — проверь, нет ли опечатки."
        return s, None
    if kind == "ogrn":
        s = re.sub(r"[\s-]", "", v)
        if form == "Самозанятый":
            return None, "У самозанятого нет ОГРН — оставь поле пустым («-»)."
        if not s.isdigit():
            return None, "ОГРН — только цифры."
        need = 13 if form == "ООО" else 15 if form == "ИП" else None
        if need and len(s) != need:
            return None, (f"У ООО ОГРН — 13 цифр, а пришло {len(s)}." if need == 13
                          else f"У ИП ОГРНИП — 15 цифр, а пришло {len(s)}.")
        if len(s) not in (13, 15):
            return None, "ОГРН — 13 цифр, ОГРНИП — 15 цифр."
        if not ogrn_ok(s):
            return None, "Не сходится контрольная цифра ОГРН — проверь, нет ли опечатки."
        return s, None
    if kind == "email":
        return (v, None) if EMAIL_RE.match(v) and len(v) <= 254 else (None, "Это не похоже на почту: name@site.ru")
    if kind == "phone":
        digits = re.sub(r"\D", "", v)
        if v.startswith("+") and 10 <= len(digits) <= 15:
            return "+" + ("7" + digits[1:] if len(digits) == 11 and digits[0] == "8" else digits), None
        if len(digits) == 11 and digits[0] in "78":
            return "+7" + digits[1:], None
        if len(digits) == 10 and digits[0] == "9":
            return "+7" + digits, None
        return None, "Это не похоже на телефон: +7 999 123-45-67"
    if kind == "url":
        u = v if "://" in v else "https://" + v.lstrip("/")
        m = re.match(r"^https://([A-Za-z0-9Ѐ-ӿ.-]+)(:\d+)?(/\S*)?$", u)
        if not m or "." not in m.group(1) or m.group(1).startswith(".") or len(u) > 500:
            return None, "Нужна ссылка вида https://site.ru/oferta (только https)."
        return u, None
    return v, None


# ---------- реквизиты из панели и переменных ----------
def merge(panel, env):
    """Реквизиты: введённое в панели главнее переменных хостинга (LEGAL_*, SUPPORT_*, *_URL, COOKIES_URL)."""
    panel = panel if isinstance(panel, dict) else {}
    out = {}
    for key, (_, _, env_name) in FIELDS.items():
        val = str(panel.get(key) or "").strip() or str((env or {}).get(env_name) or "").strip()
        if val and key == "form":
            val = validate("form", val)[0] or ""
        out[key] = val
    return out


def version(info):
    """Версия того, с чем соглашается покупатель: документы и сам продавец. Поменялись — согласие заново.
    Почта и телефон поддержки на согласие не влияют."""
    keys = ("form", "name", "inn", "ogrn", "offer_url", "privacy_url", "refund_url")
    raw = json.dumps([DOCS_VER] + [str(info.get(k) or "") for k in keys], ensure_ascii=False)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def title(info):
    """«ИП Иванов Иван Иванович» (форму добавляем, если её нет в наименовании)."""
    name, form = info.get("name") or "", info.get("form") or ""
    if form and name and not name.lower().startswith(form.lower()):
        return f"{form} {name}"
    return name


def requisites(info):
    """Реквизиты продавца несколькими строками (пусто — ничего не заполнено)."""
    lines = []
    if title(info):
        lines.append(title(info))
    ids = []
    if info.get("inn"):
        ids.append(f"ИНН {info['inn']}")
    if info.get("ogrn") and info.get("form") != "Самозанятый":      # у самозанятого ОГРН нет
        ids.append(f"{'ОГРНИП' if len(info['ogrn']) == 15 else 'ОГРН'} {info['ogrn']}")
    if ids:
        lines.append(" · ".join(ids))
    if info.get("address"):
        lines.append(f"Адрес: {info['address']}")
    contacts = [x for x in (info.get("email") and f"Почта: {info['email']}",
                            info.get("phone") and f"Телефон: {info['phone']}") if x]
    if contacts:
        lines.append(" · ".join(contacts))
    return "\n".join(lines)


def documents(info):
    """[(название, ссылка)] — документы, на которые продавец дал ссылки."""
    return [(name, info[key]) for key, name in DOCS if info.get(key)]


def mismatch(info):
    """Сохранённые ИНН и ОГРН, которые не подходят к выбранной форме (поменяли ИП на ООО и т.п.)."""
    form = info.get("form") or ""
    out = []
    for key in ("inn", "ogrn"):
        if info.get(key):
            err = validate(key, info[key], form)[1]
            if err:
                out.append(f"{FIELDS[key][0]}: {err}")
    return out


def missing(info):
    """Что ещё не заполнено из важного (покупатель должен видеть продавца и документы)."""
    out = [FIELDS[k][0] for k in ("name", "inn", "offer_url", "privacy_url", "refund_url") if not info.get(k)]
    if not info.get("email") and not info.get("phone"):
        out.append("Почта или телефон поддержки")
    return out


def privacy_text(info, contact=""):
    """Встроенное «что хранит бот» — коротко и честно (152-ФЗ): кто оператор, какие данные, зачем, кому, сколько,
    как удалить. Полный текст политики — по ссылке продавца, если она есть."""
    who = title(info)
    op = (f"{who}" + (f", ИНН {info['inn']}" if info.get("inn") else "") + (f", {info['address']}" if info.get("address")
                                                                              else "")) if who else \
        (f"продавец бота ({contact})" if contact else "продавец бота")
    reach = info.get("email") or info.get("phone") or contact
    lines = ["🔐 Какие данные хранит бот (политика конфиденциальности — кратко)", "",
             f"Оператор персональных данных: {op}.", "",
             "Что хранится и зачем:",
             "• ID, имя и ник в MAX, ID чата с ботом — чтобы узнавать тебя и отвечать;",
             "• твои каналы MAX, каналы-источники, настройки и очередь постов — без них бот не работает;",
             "• email — только если ты сам указал его для чека об оплате (54-ФЗ);",
             "• телефон — только если ты сам поделился контактом;",
             "• сведения об оплатах: тариф, сумма, дата. Данные банковской карты бот не получает — оплату "
             "принимает ЮKassa;",
             "• если подключаешь свой Telegram — файл входа хранится на сервере бота.", "",
             "Кому передаётся: ЮKassa — для оплаты и чека; сервисам ИИ (Gemini, OpenRouter, DeepSeek) — только "
             "тексты постов и черновиков, если ты пользуешься ИИ-функциями; поисковикам — только слова из постов "
             "канала для «🔥 Трендов». Твои ID, имя, почта и телефон им не передаются. Данные не продаются.", "",
             "Сколько хранится: пока пользуешься ботом; сведения об оплатах — столько, сколько требует закон.", "",
             "Твои права (152-ФЗ): узнать, какие данные о тебе есть, исправить или удалить их — кнопка "
             "«🗑 Запросить удаление моих данных»" + (f" или напиши: {reach}" if reach else "")
             + ". Удалим в течение 30 дней и сообщим."]
    if info.get("privacy_url"):
        lines += ["", f"Полный текст политики: {info['privacy_url']}"]
    return "\n".join(lines)
