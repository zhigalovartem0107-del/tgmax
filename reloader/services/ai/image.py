"""🎨 Фон для карточек аналитики, нарисованный ИИ (OpenRouter, модели с выводом картинок).

Запрос — обычный chat/completions с modalities ["image", "text"]; картинка приходит в
choices[0].message.images[*].image_url.url как data:image/...;base64,…  Нет ключа, лимит, ошибка — None,
и бот рисует фон сам (процедурный узор)."""
import base64
import re

MODEL = "google/gemini-2.5-flash-image"
PROMPT = ("Abstract premium background for an analytics dashboard banner: smooth flowing gradient waves, soft glow, "
          "deep purple, violet and blue tones, subtle depth, modern minimal style. Absolutely no text, no letters, "
          "no numbers, no logos, no people. Mood inspired by: ")


def prompt_for(topic):
    topic = re.sub(r"[^\w\s\-—,.!?]", " ", str(topic or ""))[:80].strip()
    return PROMPT + (topic or "media channel network")


def extract_image(data):
    """Байты картинки из ответа OpenRouter (или None)."""
    msg = ((data.get("choices") or [{}])[0].get("message") or {})
    for im in msg.get("images") or []:
        url = ((im or {}).get("image_url") or {}).get("url") or ""
        m = re.match(r"data:image/[\w.+-]+;base64,(.+)$", url, re.S)
        if m:
            try:
                return base64.b64decode(m.group(1))
            except ValueError:
                return None
    return None


def generate(topic, key, http, url="https://openrouter.ai/api/v1", model=MODEL, timeout=60):
    """Картинка-фон (байты PNG/JPEG) или None. http — requests.Session бота."""
    if not key:
        return None
    try:
        r = http.post(f"{url.rstrip('/')}/chat/completions", headers={"Authorization": f"Bearer {key}"},
                      json={"model": model, "modalities": ["image", "text"],
                            "messages": [{"role": "user", "content": prompt_for(topic)}]}, timeout=timeout)
        if r.status_code != 200:
            return None
        return extract_image(r.json())
    except Exception:
        return None
