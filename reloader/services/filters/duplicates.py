"""Поиск повторов по пересылке, медиа, тексту и смыслу."""

import math
import re
from collections import Counter
from difflib import SequenceMatcher
from functools import lru_cache
from itertools import chain


def norm(text: str) -> str:
    """Текст без ссылок, знаков и эмодзи — для сравнения постов."""
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"https?://\S+", "", text)
    text = text.lower().replace("ё", "е")
    return " ".join(re.findall(r"\w+", text))[:1000]


STOP_WORDS = set("""и в во не что он на я с со как а то все она так его но да ты к у же вы за бы по
только ее мне было вот от меня еще нет о из ему теперь когда даже ну вдруг ли если уже или ни быть был
него до вас нибудь опять уж вам ведь там потом себя ничего ей может они тут где есть надо ней для мы
тебя их чем была сам чтоб без будто чего раз тоже себе под будет ж тогда кто этот того потому этого
какой совсем ним здесь этом один почти мой тем чтобы нее сейчас были куда зачем всех никогда можно при
наконец два об другой хоть после над больше тот через эти нас про всего них какая много разве три эту
моя впрочем хорошо свою этой перед иногда лучше чуть том нельзя такой им более всегда конечно всю между
это стали стал будут будет также уже пока ещё новый новая новые the a an of to in is are for on and
with at by from that this it be как вам такой список""".split())
# слишком общие для темы слова: учитываются, но с малым весом
GENERIC = {"roblox", "роблокс", "robux", "робукс", "офици", "новос", "обнов", "добав",
           "игра", "игр", "игры", "ивент", "событ", "анонс", "стало", "извес"}


@lru_cache(maxsize=4096)
def terms(text: str):
    """Смысловые слова поста: без мусора, с грубой обрезкой окончаний (результат кэшируется)."""
    out = set()
    for w in re.findall(r"[a-zа-я0-9]+", text.lower().replace("ё", "е")):
        if w in STOP_WORDS or (len(w) < 2 and not w.isdigit()):
            continue
        if "а" <= w[0] <= "я":
            w = w[:5]              # "хэллоуинском" и "хэллоуинскую" -> "хэллоу"
        out.add(w)
    return frozenset(out)


@lru_cache(maxsize=16)
def history_frequencies(norms):
    return Counter(word for text in norms for word in terms(text))


def _meaning_score(ta, tb, shared, df, n_hist):
    n = n_hist + 2

    def weight(word):
        d = df.get(word, 0) + (word in ta) + (word in tb)
        idf = math.log((n + 1) / (d + 0.5)) + 1
        return idf * (0.3 if word in GENERIC else 1.0)

    small = ta if len(ta) <= len(tb) else tb
    total = sum(weight(word) for word in small)
    common = sum(weight(word) for word in shared)
    return common / total if total > 0 else 0.0


def meaning_match(a, b, df, n_hist, threshold):
    ta, tb = terms(a), terms(b)
    if len(ta) < 3 or len(tb) < 3:
        return False
    shared = ta & tb
    return len(shared - GENERIC) >= 2 and _meaning_score(ta, tb, shared, df, n_hist) >= threshold


@lru_cache(maxsize=1024)
def _text_counts(text):
    return Counter(text), frozenset(text.split())


def similar(a, b, threshold):
    if not a or not b:
        return False
    if a == b:
        return True
    shortest = min(len(a), len(b))
    if shortest < 25:  # короткие тексты сравниваются только на точное совпадение
        return False
    length = len(a) + len(b)
    chars_a, words_a = _text_counts(a)
    chars_b, words_b = _text_counts(b)
    if 2.0 * shortest / length >= threshold:
        # Та же верхняя граница, что у quick_ratio, с повторным использованием счётчиков.
        common_chars = sum(min(count, chars_b[char]) for char, count in chars_a.items())
        if 2.0 * common_chars / length >= threshold and SequenceMatcher(None, a, b).ratio() >= threshold:
            return True
    common_words = len(words_a & words_b)
    return common_words / max(1, len(words_a) + len(words_b) - common_words) >= 0.7


def find_duplicate(entry, history, *, strict, similarity, meaning):
    if not history:
        return None
    text = entry["norm"]
    forward = set(entry["fwd"])
    image_limit = 120 if strict else 25
    images = set(entry["img"]) if len(text) < image_limit else set()
    query_terms = terms(text)
    frequencies = None
    for item in history:
        if forward and not forward.isdisjoint(item.get("fwd", ())):
            return item
        if images and not images.isdisjoint(item.get("img", ())):
            return item
        # Сохраняем порядок проверок, включая сохранённые пересказы записи.
        for alternative in chain((item.get("norm", ""),), item.get("alts", ())):
            if similar(text, alternative, similarity):
                return item
            candidate = terms(alternative)
            if len(query_terms) < 3 or len(candidate) < 3:
                continue
            shared = query_terms & candidate
            if len(shared - GENERIC) < 2:
                continue
            if frequencies is None:
                frequencies = history_frequencies(tuple(x.get("norm", "") for x in history))
            if _meaning_score(query_terms, candidate, shared, frequencies, len(history)) >= meaning:
                return item
    return None
