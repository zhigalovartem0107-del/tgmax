"""Кнопки MAX: обычная (callback), ссылка; подписи вкл/выкл."""


def B(text, payload, intent=None):
    """Кнопка-обработчик. intent — цвет кнопки в MAX: "positive" (зелёная), "negative" (красная); None — обычная."""
    btn = {"type": "callback", "text": text, "payload": payload}
    if intent:
        btn["intent"] = intent
    return btn


def LB(text, url):
    """Кнопка-ссылка (откроет страницу оплаты и т.п.)."""
    return {"type": "link", "text": text, "url": url}


def on_off(v):
    return "ВКЛ ✅" if v else "ВЫКЛ ⛔"
