"""ИИ-пересказ поста: Gemini → OpenRouter → DeepSeek (у кого есть ключи). Все ключи всех исчерпаны — AiUnavailable."""
from bot_app.core import errors as _errors
from bot_app.services.ai.keys import AllKeysExhausted, KeyRejected, RateLimited

ORDER = ("gemini", "openrouter", "deepseek")
DEFAULTS = {
    "gemini": {"url": "https://generativelanguage.googleapis.com/v1beta", "model": "gemini-flash-latest"},
    "openrouter": {"url": "https://openrouter.ai/api/v1", "model": "openrouter/free"},
    "deepseek": {"url": "https://api.deepseek.com", "model": "deepseek-chat"},
}
PROMPT = ("Перескажи этот пост для канала своими словами, на том же языке. Сохрани смысл, факты, цифры, даты, "
          "ссылки, @упоминания, #хэштеги, эмодзи и разметку (**жирный**, _курсив_). Ничего не добавляй от себя, "
          "без вступлений и кавычек — ответь только готовым текстом поста.\n\nПост:\n")


class AiUnavailable(RuntimeError):
    """Ни один провайдер не дал пересказ: все ключи упёрлись в лимит, не приняты или сервис не ответил.
    kinds — причины по всем ключам всех провайдеров; code — код ошибки (ERR_AI_429, ERR_AI_TIMEOUT, …)."""

    def __init__(self, detail, kinds=()):
        super().__init__(detail)
        self.kinds = list(kinds)
        self.code = _errors.ai_code(self.kinds)


def _retry_after(resp):
    try:
        return float(resp.headers.get("Retry-After"))
    except (TypeError, ValueError):
        return None


def _clean(text):
    text = (text or "").strip()
    for a, b in (('"', '"'), ("«", "»"), ("“", "”")):
        if len(text) > 1 and text.startswith(a) and text.endswith(b):
            text = text[1:-1].strip()
    return text


def call_provider(provider, key, text, http, url, model, timeout=60, prompt=PROMPT):
    if provider == "gemini":
        r = http.post(f"{url}/models/{model}:generateContent", headers={"x-goog-api-key": key},
                      json={"contents": [{"parts": [{"text": prompt + text}]}]}, timeout=timeout)
    else:
        r = http.post(f"{url}/chat/completions", headers={"Authorization": f"Bearer {key}"},
                      json={"model": model, "messages": [{"role": "user", "content": prompt + text}]}, timeout=timeout)
    if r.status_code == 429:
        raise RateLimited(_retry_after(r))
    if r.status_code in (401, 403):
        raise KeyRejected(f"{r.status_code}: {r.text[:120]}")
    if r.status_code >= 300:
        raise RuntimeError(f"{r.status_code}: {r.text[:160]}")
    data = r.json()
    if provider == "gemini":
        parts = ((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
        out = "".join(p.get("text", "") for p in parts)
    else:
        out = (((data.get("choices") or [{}])[0].get("message") or {}).get("content")) or ""
    out = _clean(out)
    if not out:
        raise RuntimeError("пустой ответ")
    return out


def rewrite(text, rotators, http, settings, log=print, prompt=PROMPT, timeout=60):
    """Пересказ: провайдеры по очереди ORDER, у каждого — ключи по кругу (429 — следующий ключ).
    prompt — другое задание тем же ключам (например, короткий совет к посту), timeout — сколько ждать ответа."""
    reasons, kinds = [], []
    for provider in ORDER:
        rot = rotators.get(provider)
        if rot is None or not rot.keys():
            continue
        cfg = settings.get(provider) or DEFAULTS[provider]
        try:
            return rot.run(lambda key, p=provider, c=cfg: call_provider(p, key, text, http, c["url"], c["model"],
                                                                        timeout, prompt))
        except AllKeysExhausted as e:
            log(_errors.line(e.code, f"🤖 ИИ {provider}: все ключи исчерпаны — {e}"))
            reasons.append(str(e))
            kinds += e.kinds
    raise AiUnavailable("; ".join(reasons) or "ключей ИИ нет", kinds)


ADVICE = ("Ты — редактор каналов в мессенджере MAX. Оцени черновик поста коротко, по-русски, 2–3 пункта: что "
          "зацепит читателя и что улучшить (заголовок, первая строка, призыв, длина). Без вступлений и без "
          "переписывания поста целиком, не больше 400 знаков.\n\nЧерновик:\n")


def ask(text, rotators, http, settings, log=print, prompt=ADVICE, timeout=25):
    """Короткий ответ ИИ на своё задание (по умолчанию — совет к черновику поста). Нет ключей — AiUnavailable."""
    return rewrite(text, rotators, http, settings, log, prompt=prompt, timeout=timeout)
