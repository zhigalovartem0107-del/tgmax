"""Клиент Bot API. Ошибки не включают URL с токеном или тело запроса."""

import requests


class BotAPIError(Exception):
    def __init__(self, code=None, *, retry_after=0):
        super().__init__(f"Telegram Bot API: {code or 'нет подтверждения'}")
        self.code = code
        self.retry_after = retry_after


class BotAPI:
    def __init__(self, token, session):
        self.url = f"https://api.telegram.org/bot{token}"
        self.session = session

    def call(self, method, **params):
        timeout = params.get("timeout", 0) + 10
        try:
            response = self.session.post(f"{self.url}/{method}", json=params, timeout=(5, timeout))
            envelope = response.json()
        except (requests.RequestException, ValueError):
            raise BotAPIError() from None
        if not isinstance(envelope, dict):
            raise BotAPIError()
        if envelope.get("ok") is True and "result" in envelope:
            return envelope["result"]
        code = envelope.get("error_code")
        retry_after = (envelope.get("parameters") or {}).get("retry_after", 0)
        if not isinstance(code, int) or code >= 500:
            code = None
        raise BotAPIError(code, retry_after=retry_after if isinstance(retry_after, int) else 0)
