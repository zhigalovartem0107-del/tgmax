"""Готовый черновик новости с проверяемыми ссылками на публикации."""
from urllib.parse import urlsplit

import requests

from bot_app.services.trends.search import Searcher, ddg_news, google_news
from bot_app.services.trends.worker import group


def news_search():
    return Searcher([("DuckDuckGo", lambda q, lang: ddg_news(q, lang, timeout=4)),
                     ("Google News", lambda q, lang: google_news(q, requests, lang, timeout=4))],
                    deadline=5, pause=None)


def news_post(items, now):
    fresh = []
    for item in items:
        if not item.get("t") or not 0 <= now - item["t"] <= 86400:
            continue
        try:
            url = urlsplit(item["url"])
            if url.scheme not in ("https", "http") or not url.hostname or url.username is not None:
                continue
        except ValueError:
            continue
        fresh.append(item)
    groups = group(fresh)
    if not groups:
        return ""
    chosen = max(groups, key=lambda g: (len(g["others"]), g["item"]["t"]))
    article = chosen["item"]
    parts = [article["title"]]
    summary = article.get("body") or ""
    if summary and not summary.startswith(article["title"]):
        parts.append(summary)
    sources = []
    for story in (article, *chosen["others"]):
        line = (story.get("source") or urlsplit(story["url"]).hostname) + " — " + story["url"]
        if sum(len(s) for s in parts + sources + [line]) > 1930:
            break
        sources.append(line)
        if len(sources) == 3:
            break
    return "\n\n".join([*parts, "Источники\n" + "\n".join(sources)]) if sources else ""
