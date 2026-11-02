"""Кнопки MAX: обычная (callback), ссылка; подписи вкл/выкл."""


def B(text, payload):
    return {"type": "callback", "text": text, "payload": payload}


def LB(text, url):
    """Кнопка-ссылка (откроет страницу оплаты и т.п.)."""
    return {"type": "link", "text": text, "url": url}


def on_off(v):
    return "ВКЛ ✅" if v else "ВЫКЛ ⛔"
