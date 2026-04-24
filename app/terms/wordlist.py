from __future__ import annotations

from functools import lru_cache

from app.settings.config import DATA_ROOT


WORDLIST_ROOT = DATA_ROOT / "dict"
WORDLIST_BASE_URL = "https://raw.githubusercontent.com/Amunage/NovelTrans/main/data/dict"
LANGUAGE_WORDLIST_FILENAMES = {
    "japanese": "japanese_dict.txt",
    "chinese": "chinese_dict.txt",
}


@lru_cache(maxsize=None)
def load_word_set(filename: str) -> frozenset[str]:
    path = get_wordlist_path(filename)
    if not path.is_file():
        return frozenset()

    words: set[str] = set()
    for raw_line in path.read_text(encoding="utf-8-sig", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        word = line.split()[0].strip()
        if word:
            words.add(word)

    return frozenset(words)


def has_word(filename: str, word: str) -> bool:
    return word in load_word_set(filename)


def get_language_wordlist_filename(language_key: str) -> str | None:
    return LANGUAGE_WORDLIST_FILENAMES.get(language_key)


def get_wordlist_download_url(filename: str) -> str:
    return f"{WORDLIST_BASE_URL}/{filename}"


def get_wordlist_path(filename: str):
    return WORDLIST_ROOT / filename


def clear_wordlist_cache() -> None:
    load_word_set.cache_clear()


__all__ = [
    "WORDLIST_ROOT",
    "clear_wordlist_cache",
    "get_language_wordlist_filename",
    "get_wordlist_download_url",
    "get_wordlist_path",
    "has_word",
    "load_word_set",
]
