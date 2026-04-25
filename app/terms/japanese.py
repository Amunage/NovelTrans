from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from app.settings.prompt import with_user_prompt
from app.terms.base import GLOSSARY_EXAMPLE_SENTENCE_COUNT, GlossaryLanguageSupport
from app.terms.candidate import TermExtractionConfig, extract_candidates
from app.terms.wordlist import has_word


TERM_CHAR_CLASS = r"\u4e00-\u9faf\u3005\u3006\u30f5\u30f6\u30a1-\u30f4\u30fc\u30fb"
TERM_PATTERN = re.compile(rf"[{TERM_CHAR_CLASS}]+(?:\s+[{TERM_CHAR_CLASS}]+)*")
KATAKANA_ONLY_PATTERN = re.compile(r"^[\u30a1-\u30f4\u30fc\u30fb]+(?:\s+[\u30a1-\u30f4\u30fc\u30fb]+)*$")
KANJI_OR_KATAKANA_PATTERN = re.compile(r"[\u4e00-\u9faf\u30a1-\u30f4]")
HIRAGANA_PATTERN = re.compile(r"[\u3041-\u3096]")
KANJI_PATTERN = re.compile(r"[\u4e00-\u9faf]")
KATAKANA_PATTERN = re.compile(r"[\u30a1-\u30f4]")

MIN_TERM_COUNT = 5
MIN_FILE_COUNT = 1
MAX_CANDIDATES = 300
MIN_TERM_SCORE = 4.0
JAPANESE_DICT_FILENAME = "japanese_dict.txt"
NAME_SUFFIXES = (
    "さん",
    "君",
    "くん",
    "ちゃん",
    "殿",
    "先輩",
    "先生",
    "氏",
    "選手",
    "トレーナー",
)
NAME_SUFFIX_PATTERN = re.compile(
    rf"[{TERM_CHAR_CLASS}]{{2,}}(?:\s+)?(?:{'|'.join(re.escape(suffix) for suffix in sorted(NAME_SUFFIXES, key=len, reverse=True))})"
)
NAME_LIKE_SUFFIXES = (
    "子",
    "美",
    "香",
    "菜",
    "花",
    "華",
    "奈",
    "希",
    "乃",
    "男",
    "郎",
)
GLOSSARY_SYSTEM_INSTRUCTIONS = [
    "You are reviewing Japanese glossary candidates for a Korean novel translation glossary.",
    "Keep only real proper nouns, person names, place names, organizations, schools, techniques, titles, item names, and fixed story-specific terms.",
    "Remove everyday vocabulary, abstract nouns, scene words, emotions, partial stems, ordinary katakana loanwords, and non-terms.",
    "Return a JSON object only.",
    "Keys must stay in Japanese exactly as provided.",
    "Values must be concise, natural Korean glossary entries.",
    "Do not include explanations or markdown.",
]

def _normalize_term(term: str) -> str:
    normalized = term.strip("・")
    normalized = re.sub(r"\s+", " ", normalized)
    normalized = re.sub(r"ー{2,}", "ー", normalized)
    return normalized.strip()


def _is_kanji_only(text: str) -> bool:
    return bool(text) and re.fullmatch(r"[\u4e00-\u9faf]+", text) is not None


def _has_kanji(text: str) -> bool:
    return KANJI_PATTERN.search(text) is not None


def _has_katakana(text: str) -> bool:
    return KATAKANA_PATTERN.search(text) is not None


def _is_mixed_kanji_katakana(term: str) -> bool:
    return _has_kanji(term) and _has_katakana(term)


def _has_name_suffix(term: str) -> bool:
    compact_term = term.replace(" ", "")
    for suffix in NAME_SUFFIXES:
        if compact_term.endswith(suffix):
            stem = compact_term[: -len(suffix)]
            return KANJI_OR_KATAKANA_PATTERN.search(stem) is not None
    return False


def _strip_name_suffix(term: str) -> str:
    normalized = _normalize_term(term)
    for suffix in sorted(NAME_SUFFIXES, key=len, reverse=True):
        if normalized.endswith(suffix):
            stem = _normalize_term(normalized[: -len(suffix)])
            if stem and KANJI_OR_KATAKANA_PATTERN.search(stem):
                return stem
    return normalized


def _merge_name_suffix_variants(candidates: dict[str, list[str]]) -> dict[str, list[str]]:
    merged: dict[str, list[str]] = {}
    for term, examples in candidates.items():
        canonical_term = _strip_name_suffix(term)
        merged_examples = merged.setdefault(canonical_term, [])
        for example in examples:
            if example not in merged_examples:
                merged_examples.append(example)
            if len(merged_examples) >= GLOSSARY_EXAMPLE_SENTENCE_COUNT:
                break
    return merged


def _find_contained_name_term(term: str, name_terms: list[str]) -> str | None:
    for name_term in name_terms:
        if name_term == term or name_term not in term:
            continue
        return name_term
    return None


def _merge_contained_name_variants(candidates: dict[str, list[str]]) -> dict[str, list[str]]:
    name_terms = [
        term
        for term in candidates
        if KATAKANA_ONLY_PATTERN.fullmatch(term) or _has_name_pattern(term)
    ]
    name_terms.sort(key=len, reverse=True)

    merged: dict[str, list[str]] = {}
    for term, examples in candidates.items():
        is_name_candidate = term in name_terms
        canonical_term = term if is_name_candidate else (_find_contained_name_term(term, name_terms) or term)
        merged_examples = merged.setdefault(canonical_term, [])
        for example in examples:
            if example not in merged_examples:
                merged_examples.append(example)
            if len(merged_examples) >= GLOSSARY_EXAMPLE_SENTENCE_COUNT:
                break
    return merged


def _iter_term_matches(text: str) -> Iterable[re.Match[str]]:
    yield from TERM_PATTERN.finditer(text)
    yield from NAME_SUFFIX_PATTERN.finditer(text)


def _has_name_pattern(term: str) -> bool:
    compact_term = term.replace(" ", "")
    if _has_name_suffix(term):
        return True
    if _is_mixed_kanji_katakana(term):
        return (" " in term) or ("・" in term)
    if KATAKANA_ONLY_PATTERN.fullmatch(term):
        return (" " in term) or ("・" in term)
    if compact_term.endswith(NAME_LIKE_SUFFIXES):
        return True
    return False


def _is_valid_term(term: str) -> bool:
    if len(term) < 2:
        return False
    if not KANJI_OR_KATAKANA_PATTERN.search(term):
        return False
    if all(char in {"ー", "・", " "} for char in term):
        return False

    compact_term = term.replace(" ", "")
    if len(compact_term) < 2:
        return False
    if KATAKANA_ONLY_PATTERN.fullmatch(term) and len(compact_term) < 4:
        return False
    if _is_mixed_kanji_katakana(term) and (" " not in term) and ("・" not in term) and len(compact_term) < 5:
        return False

    parts = [part for part in term.split(" ") if part]
    if any(len(part) == 1 and not re.search(r"[\u4e00-\u9faf]", part) for part in parts):
        return False

    compact_no_separators = term.replace(" ", "").replace("・", "")
    if _is_kanji_only(compact_no_separators):
        has_separator = (" " in term) or ("・" in term)
        if not has_separator and not _has_name_pattern(term) and len(compact_no_separators) < 3:
            return False

    return True


def _is_embedded_kanji_stem(text: str, start: int, end: int, term: str) -> bool:
    compact_term = term.replace(" ", "").replace("・", "")
    if not _is_kanji_only(compact_term):
        return False

    if len(compact_term) <= 2:
        if _has_name_pattern(term):
            return False
        next_char = text[end] if end < len(text) else ""
        next_next_char = text[end + 1] if end + 1 < len(text) else ""
        if next_char == " " and _is_kanji_only(next_next_char):
            return False
        if next_char == "・" and _is_kanji_only(next_next_char):
            return False
        return True

    prev_char = text[start - 1] if start > 0 else ""
    next_char = text[end] if end < len(text) else ""
    if next_char and HIRAGANA_PATTERN.fullmatch(next_char):
        return True
    if prev_char and HIRAGANA_PATTERN.fullmatch(prev_char):
        return True
    return False


def _is_dictionary_word(term: str) -> bool:
    compact_term = term.replace(" ", "").replace("・", "")
    if len(compact_term) < 2:
        return False
    return has_word(JAPANESE_DICT_FILENAME, compact_term)


def _has_honorific_context(term: str, sentences: list[str]) -> bool:
    for sentence in sentences:
        for suffix in NAME_SUFFIXES:
            if f"{term}{suffix}" in sentence or f"{term} {suffix}" in sentence:
                return True
    return False


def _has_name_like_usage(term: str, sentences: list[str]) -> bool:
    return _has_name_pattern(term) or _has_honorific_context(term, sentences)


def _has_cooccurring_proper_noun(term: str, sentences: list[str]) -> bool:
    for sentence in sentences:
        found_terms: set[str] = set()
        for match in _iter_term_matches(sentence):
            candidate = _normalize_term(match.group(0))
            if candidate == term or not _is_valid_term(candidate):
                continue
            found_terms.add(candidate)
        if found_terms:
            return True
    return False


def _score_term(term: str, count: int, file_count: int, sentences: list[str]) -> float:
    score = 0.0
    score += min(count, 8) * 0.35
    score += min(file_count, 6) * 1.2
    if _is_mixed_kanji_katakana(term):
        score -= 1.0
    if _has_name_pattern(term):
        score += 1.4
    if _has_honorific_context(term, sentences):
        score += 2.2
    if _has_cooccurring_proper_noun(term, sentences):
        score += 1.2

    compact_term = term.replace(" ", "")
    if re.fullmatch(r"[\u4e00-\u9faf]+", compact_term) and len(compact_term) >= 2:
        score += 0.8
    if " " in term:
        score += 0.6

    return score


def _reject_match(text: str, match: re.Match[str], term: str) -> bool:
    return _is_embedded_kanji_stem(text, match.start(), match.end(), term)


def _reject_candidate(term: str, sentences: list[str]) -> bool:
    return _is_dictionary_word(term) and not _has_name_like_usage(term, sentences)


def build_refine_prompt(novel_name: str, candidates: list[tuple[str, list[str]]]) -> str:
    prompt_lines = GLOSSARY_SYSTEM_INSTRUCTIONS.copy()
    prompt_lines.append(f"Novel: {novel_name}")
    prompt_lines.append("Review the following candidate glossary entries.")
    prompt_lines.append("Each entry is formatted as: Japanese term => example sentences")
    prompt_lines.append("<candidates>")
    for term, examples in candidates:
        prompt_lines.append(f"{term} =>")
        for index, example in enumerate(examples, start=1):
            prompt_lines.append(f"  {index}. {example}")
    prompt_lines.append("</candidates>")
    prompt_lines.append(
        'Return strict JSON like {"トレセン学園":"트레센 학원","セイウンスカイ":"세이운 스카이"} and include only accepted glossary entries.'
    )
    return "\n".join(with_user_prompt(prompt_lines, "glossary_instructions"))


def extract_glossary_candidates(novel_dir: Path, min_term_count: int = MIN_TERM_COUNT) -> dict[str, list[str]]:
    candidates = extract_candidates(
        novel_dir,
        TermExtractionConfig(
            iter_matches=_iter_term_matches,
            normalize_term=_normalize_term,
            is_valid_term=_is_valid_term,
            reject_match=_reject_match,
            reject_candidate=_reject_candidate,
            score_term=_score_term,
            min_term_count=min_term_count,
            min_file_count=MIN_FILE_COUNT,
            min_term_score=MIN_TERM_SCORE,
            max_candidates=MAX_CANDIDATES,
        ),
    )
    candidates = _merge_name_suffix_variants(candidates)
    return _merge_contained_name_variants(candidates)


JAPANESE_GLOSSARY = GlossaryLanguageSupport(
    key="japanese",
    source_label="Japanese",
    extract_glossary_candidates=extract_glossary_candidates,
    build_refine_prompt=build_refine_prompt,
    default_min_term_count=MIN_TERM_COUNT,
)
