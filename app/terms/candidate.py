from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from app.terms.base import _choose_example_sentences, _normalize_sentence, _split_sentences
from app.utils import find_chapter_files, parse_source_file


MatchIterator = Callable[[str], Iterable[re.Match[str]]]
NormalizeTerm = Callable[[str], str]
ValidateTerm = Callable[[str], bool]
RejectMatch = Callable[[str, re.Match[str], str], bool]
RejectCandidate = Callable[[str, list[str]], bool]
ScoreTerm = Callable[[str, int, int, list[str]], float]


@dataclass(frozen=True)
class TermExtractionConfig:
    iter_matches: MatchIterator
    normalize_term: NormalizeTerm
    is_valid_term: ValidateTerm
    score_term: ScoreTerm
    min_term_count: int
    min_file_count: int
    min_term_score: float
    max_candidates: int
    reject_match: RejectMatch | None = None
    reject_candidate: RejectCandidate | None = None


def extract_candidates(novel_dir: Path, config: TermExtractionConfig) -> dict[str, list[str]]:
    chapter_files = find_chapter_files(novel_dir)
    term_counts: Counter[str] = Counter()
    file_counts: Counter[str] = Counter()
    sentences_by_term: dict[str, list[str]] = defaultdict(list)

    for chapter_file in chapter_files:
        document = parse_source_file(chapter_file)
        chapter_text = "\n".join([document.title, document.body])
        sentences = _split_sentences(chapter_text)
        seen_terms_in_file: set[str] = set()

        for match in config.iter_matches(chapter_text):
            term = config.normalize_term(match.group(0))
            if not config.is_valid_term(term):
                continue
            if config.reject_match is not None and config.reject_match(chapter_text, match, term):
                continue
            term_counts[term] += 1
            seen_terms_in_file.add(term)

        for term in seen_terms_in_file:
            file_counts[term] += 1

        for sentence in sentences:
            found_terms: set[str] = set()
            for match in config.iter_matches(sentence):
                term = config.normalize_term(match.group(0))
                if not config.is_valid_term(term):
                    continue
                if config.reject_match is not None and config.reject_match(sentence, match, term):
                    continue
                found_terms.add(term)

            for term in found_terms:
                sentences_by_term[term].append(_normalize_sentence(sentence))

    scored_candidates: list[tuple[str, list[str], float, int]] = []
    for source_order, (term, count) in enumerate(term_counts.most_common()):
        if count < config.min_term_count or file_counts[term] < config.min_file_count:
            continue

        sentences = sentences_by_term.get(term, [])
        if config.reject_candidate is not None and config.reject_candidate(term, sentences):
            continue

        term_score = config.score_term(term, count, file_counts[term], sentences)
        if term_score < config.min_term_score:
            continue

        scored_candidates.append((term, _choose_example_sentences(term, sentences), term_score, source_order))

    scored_candidates.sort(key=lambda item: (-item[2], item[3]))
    return {term: example for term, example, _, _ in scored_candidates[: config.max_candidates]}


__all__ = ["TermExtractionConfig", "extract_candidates"]
