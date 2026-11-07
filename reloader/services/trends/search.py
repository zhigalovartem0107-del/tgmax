"""Поиск свежих новостей для трендов.

Основной путь — бесплатная библиотека ddgs (бывшая duckduckgo_search): новости DuckDuckGo/Bing/Yahoo за сутки.
Запасной — RSS Google News (обычный HTTP, без библиотек): если библиотеки нет, DuckDuckGo ограничил запросы
или недоступен с сервера. Searcher помнит ответы час (одинаковые слова у разных каналов — один запрос), держит
паузу между запросами, лимит запросов в час и «отдых» поисковику, который ответил «слишком часто»."""
import random
import re
import threading
import time
import warnings
from collections import deque
from email.utils import parsedate_to_datetime
from html import unescape
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from xml.etree import ElementTree

TRACKING = re.compile(r"^(utm_\w+|fbclid|gclid|yclid|ysclid|mc_cid|mc_eid|_openstat|ref|ref_src|from|source|rss|"
                      r"utm|cmpid|ocid|spm|share|amp|outputtype)$", re.I)
_TAG = re.compile(r"<[^>]+>")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/130.0 Safari/537.36")


class SearchError(Exception):
    """Поиск не ответил (сбой сети, ошибка сайта)."""


class RateLimited(SearchError):
    """Поисковик просит не спрашивать так часто."""


class SearchUnavailable(SearchError):
    """Способ поиска недоступен на этом сервере (нет библиотеки)."""


# ---------- адреса и даты ----------
def canonical_url(url):
    """Один адрес для одной статьи: без http/https, www., m., amp-версии, якоря и меток (utm_…, fbclid…).
    https://www.site.ru/news/1/?utm_source=x#top  ->  site.ru/news/1"""
    try:
        sp = urlsplit(str(url or "").strip())
    except ValueError:
        return str(url or "").strip().lower()
    host = (sp.hostname or "").lower()
    for pre in ("www.", "m.", "amp."):
        if host.startswith(pre) and host.count(".") >= 2:
            host = host[len(pre):]
    path = re.sub(r"/(amp|amp\.html)/?$", "/", sp.path or "")
    path = re.sub(r"/{2,}", "/", path).rstrip("/")
    query = sorted((k, v) for k, v in parse_qsl(sp.query, keep_blank_values=False) if not TRACKING.match(k))
    return urlunsplit(("", host, path, urlencode(query), "")).lstrip("/")


def parse_date(value):
    """Время новости (секунды) из того, что дал поиск: число (сек/мс), ISO 8601, RFC 822 (RSS). Нет — None."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        return v / 1000 if v > 1e11 else v
    s = str(value).strip()
    if re.fullmatch(r"\d{9,13}(\.\d+)?", s):
        return parse_date(float(s))
    try:
        from datetime import datetime, timezone
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.timestamp()
    except ValueError:
        pass
    try:
        return parsedate_to_datetime(s).timestamp()
    except (TypeError, ValueError, IndexError):
        return None


def plain(html):
    return re.sub(r"\s+", " ", unescape(_TAG.sub(" ", str(html or "")))).strip()


def normalize(raw):
    """Одна новость в общем виде: {"title", "url", "canon", "body", "source", "t"}; мусор — None."""
    if not isinstance(raw, dict):
        return None
    url = str(raw.get("url") or raw.get("href") or raw.get("link") or "").strip()
    title = plain(raw.get("title"))
    if not url.lower().startswith(("http://", "https://")) or not title:
        return None
    source = plain(raw.get("source"))
    if source and title.endswith(" - " + source):       # Google News: «Заголовок - Источник»
        title = title[:-len(source) - 3].strip()
    elif not source and " - " in title:
        title, source = title.rsplit(" - ", 1)
    return {"title": title[:220], "url": url[:1500], "canon": canonical_url(url),
            "body": plain(raw.get("body") or raw.get("description") or raw.get("excerpt"))[:400],
            "source": source[:80], "t": parse_date(raw.get("date") or raw.get("pubDate") or raw.get("published"))}


# ---------- способы поиска ----------
def _ddgs_class():
    """DDGS из ddgs (Python 3.10+) или из старой duckduckgo_search; нет ни той, ни другой — None."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")              # duckduckgo_search предупреждает о переименовании
        for mod in ("ddgs", "duckduckgo_search"):
            try:
                return __import__(mod, fromlist=["DDGS"]).DDGS
            except Exception:
                continue
    return None


def ddg_news(query, lang="ru", max_results=10, timelimit="d", timeout=20, proxy=None, ddgs=None):
    """Новости за сутки через библиотеку ddgs / duckduckgo_search. ddgs — класс DDGS (для проверок)."""
    cls = ddgs or _ddgs_class()
    if cls is None:
        raise SearchUnavailable("не установлена библиотека поиска ddgs")
    region = "ru-ru" if lang == "ru" else "us-en"
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            got = cls(proxy=proxy, timeout=timeout).news(query, region=region, safesearch="moderate",
                                                         timelimit=timelimit, max_results=max_results)
    except Exception as e:
        name, msg = type(e).__name__, str(e)
        if "ratelimit" in name.lower() or "ratelimit" in msg.lower() or re.search(r"\b202\b", msg):
            raise RateLimited(f"DuckDuckGo: слишком часто ({msg[:80]})") from None
        if "no results" in msg.lower():
            return []
        raise SearchError(f"DuckDuckGo: {name}: {msg[:120]}") from None
    return list(got or [])


def google_news(query, http, lang="ru", timeout=20, max_results=15):
    """Новости за сутки из RSS Google News — без библиотек и ключей. http — requests.Session бота."""
    if lang == "ru":
        params = {"q": f"{query} when:1d", "hl": "ru", "gl": "RU", "ceid": "RU:ru"}
    else:
        params = {"q": f"{query} when:1d", "hl": "en-US", "gl": "US", "ceid": "US:en"}
    try:
        r = http.get("https://news.google.com/rss/search", params=params, timeout=timeout,
                     headers={"User-Agent": UA, "Accept": "application/rss+xml, application/xml"})
    except Exception as e:
        raise SearchError(f"Google News: нет связи ({type(e).__name__})") from None
    if r.status_code == 429:
        raise RateLimited("Google News: слишком часто")
    if r.status_code != 200:
        raise SearchError(f"Google News ответил {r.status_code}")
    try:
        root = ElementTree.fromstring((r.content or b"")[:3_000_000])
    except ElementTree.ParseError:
        raise SearchError("Google News: непонятный ответ") from None
    out = []
    for item in root.iter("item"):
        src = item.find("source")
        out.append({"title": item.findtext("title"), "url": item.findtext("link"),
                    "date": item.findtext("pubDate"), "body": item.findtext("description"),
                    "source": src.text if src is not None else ""})
        if len(out) >= max_results:
            break
    return out


# ---------- поиск с памятью, паузами и запасным путём ----------
class Searcher:
    """search(запрос, язык) -> ([новости], ошибка или None). backends — [(имя, fn(query, lang) -> [сырые])]:
    идём по порядку, пока какой-то не даст новости. Ответ помним ttl секунд; не больше max_per_hour запросов
    в сеть за час; между запросами — пауза pause (сек); поисковик, сказавший «слишком часто», отдыхает rest."""

    def __init__(self, backends, ttl=3600, max_per_hour=40, pause=(1.5, 3.5), rest=1800, fail_rest=300,
                 clock=time.time, sleep=time.sleep, rnd=None):
        self.backends = list(backends)
        self.ttl, self.max_per_hour, self.pause = ttl, max_per_hour, pause
        self.rest, self.fail_rest = rest, fail_rest
        self.clock, self.sleep, self.rnd = clock, sleep, rnd or random.Random()
        self.cache, self.resting, self.calls = {}, {}, deque()
        self.last_net = 0.0
        self.lock = threading.Lock()

    def status(self):
        now = self.clock()
        return {name: int(until - now) for name, until in self.resting.items() if until > now}

    def _wait_turn(self):
        gap = self.rnd.uniform(*self.pause) if self.pause else 0
        left = self.last_net + gap - self.clock()
        if left > 0:
            self.sleep(left)

    def search(self, query, lang="ru"):
        key = (" ".join(str(query or "").lower().split()), lang)
        if not key[0]:
            return [], None
        with self.lock:                                   # один запрос в сеть за раз — поисковики это любят
            now = self.clock()
            hit = self.cache.get(key)
            if hit and now - hit[0] < self.ttl:
                return list(hit[1]), None
            while self.calls and now - self.calls[0] > 3600:
                self.calls.popleft()
            errors, answered = [], False
            for name, fn in self.backends:
                if self.resting.get(name, 0) > now:
                    errors.append(f"{name}: отдыхает")
                    continue
                if len(self.calls) >= self.max_per_hour:
                    errors.append("лимит запросов к поиску на час")
                    break
                self._wait_turn()
                self.calls.append(self.clock())
                try:
                    raw = fn(key[0], lang)
                except RateLimited as e:
                    self.resting[name] = self.clock() + self.rest
                    errors.append(str(e))
                    continue
                except SearchUnavailable as e:
                    self.resting[name] = self.clock() + 6 * 3600
                    errors.append(str(e))
                    continue
                except Exception as e:
                    self.resting[name] = self.clock() + self.fail_rest
                    errors.append(str(e)[:160] if isinstance(e, SearchError) else f"{name}: {type(e).__name__}")
                    continue
                finally:
                    self.last_net = self.clock()
                answered = True
                items, seen = [], set()
                for r in raw or []:
                    it = normalize(r)
                    if it and it["canon"] not in seen:
                        seen.add(it["canon"])
                        items.append(it)
                if items:
                    self.cache[key] = (self.clock(), items)
                    return list(items), None
            if answered:                                  # поисковик ответил «новостей нет» — это не ошибка
                self.cache[key] = (self.clock(), [])
                return [], None
            return [], "; ".join(dict.fromkeys(errors)) or None
