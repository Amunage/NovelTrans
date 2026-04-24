from __future__ import annotations

from functools import lru_cache

from app.settings.config import DATA_ROOT


DICT_ROOT = DATA_ROOT / "dict"


@lru_cache(maxsize=None)
def load_word_set(filename: str) -> frozenset[str]:
    path = DICT_ROOT / filename
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


def has_dictionary_word(filename: str, word: str) -> bool:
    return word in load_word_set(filename)


__all__ = ["DICT_ROOT", "has_dictionary_word", "load_word_set"]
