from __future__ import annotations

import json
import re
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

try:
    from janome.tokenizer import Tokenizer
except ImportError:  # pragma: no cover - optional dependency during local edits
    Tokenizer = None

from app.client import LlamaCppServerClient, start_llama_server, stop_llama_server
from app.config import APP_ROOT, get_runtime_settings, log_runtime_event
from app.translation import TranslationConfig
from app.ui import (
    parse_command,
    render_glossary_candidate_progress_screen,
    render_glossary_complete_screen,
    render_glossary_refine_progress_screen,
    render_glossary_selection_screen,
)
from app.utils import find_chapter_files, find_source_novels, parse_source_file


TERM_CHAR_CLASS = r"\u4e00-\u9faf\u3005\u3006\u30f5\u30f6\u30a1-\u30f4\u30fc\u30fb"
TERM_PATTERN = re.compile(rf"[{TERM_CHAR_CLASS}]+(?:\s+[{TERM_CHAR_CLASS}]+)*")
KATAKANA_ONLY_PATTERN = re.compile(r"^[\u30a1-\u30f4\u30fc\u30fb]+(?:\s+[\u30a1-\u30f4\u30fc\u30fb]+)*$")
KANJI_OR_KATAKANA_PATTERN = re.compile(r"[\u4e00-\u9faf\u30a1-\u30f4]")
HIRAGANA_PATTERN = re.compile(r"[\u3041-\u3096]")
KANJI_PATTERN = re.compile(r"[\u4e00-\u9faf]")
KATAKANA_PATTERN = re.compile(r"[\u30a1-\u30f4]")
SENTENCE_SPLIT_PATTERN = re.compile(r"(?<=[\u3002\uff01\uff1f!?])\s+|\n+")
STOP_TERMS_PATH = APP_ROOT / "glossary" / "stop_terms.txt"

MIN_TERM_COUNT = 2
MIN_FILE_COUNT = 2
MAX_CANDIDATES = 300
GLOSSARY_BATCH_SIZE = 30
MIN_SENTENCE_LENGTH = 12
MAX_SENTENCE_LENGTH = 140
TARGET_SENTENCE_LENGTH = 48
MIN_TERM_SCORE = 4.0
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
_TOKENIZER: Any = None
GLOSSARY_SYSTEM_INSTRUCTIONS = [
    "You are reviewing Japanese glossary candidates for a Korean novel translation glossary.",
    "Keep only real proper nouns, person names, place names, organizations, schools, techniques, titles, item names, and fixed story-specific terms.",
    "Remove everyday vocabulary, abstract nouns, scene words, emotions, partial stems, ordinary katakana loanwords, and non-terms.",
    "Return a JSON object only.",
    "Keys must stay in Japanese exactly as provided.",
    "Values must be concise, natural Korean glossary entries.",
    "Do not include explanations or markdown.",
]
def _load_stop_terms() -> set[str]:
    stop_terms: set[str] = set()
    if not STOP_TERMS_PATH.is_file():
        return stop_terms

    for raw_line in STOP_TERMS_PATH.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        stop_terms.add(line)
    return stop_terms


def _get_tokenizer() -> Any | None:
    global _TOKENIZER

    if _TOKENIZER is False:
        return None
    if _TOKENIZER is not None:
        return _TOKENIZER
    if Tokenizer is None:
        _TOKENIZER = False
        return None

    try:
        _TOKENIZER = Tokenizer()
    except Exception:
        _TOKENIZER = False
        return None
    return _TOKENIZER


def _normalize_term(term: str) -> str:
    normalized = term.strip("・")
    normalized = re.sub(r"\s+", " ", normalized)
    normalized = re.sub(r"ー{2,}", "ー", normalized)
    return normalized.strip()


def _normalize_sentence(sentence: str) -> str:
    normalized = sentence.replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


def _is_kanji_only(text: str) -> bool:
    return bool(text) and re.fullmatch(r"[\u4e00-\u9faf]+", text) is not None


def _has_kanji(text: str) -> bool:
    return KANJI_PATTERN.search(text) is not None


def _has_katakana(text: str) -> bool:
    return KATAKANA_PATTERN.search(text) is not None


def _is_mixed_kanji_katakana(term: str) -> bool:
    return _has_kanji(term) and _has_katakana(term)


def _is_valid_term(term: str, stop_terms: set[str]) -> bool:
    if len(term) < 2:
        return False
    if term in stop_terms:
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

    # Drop cases like 仲良く / 優しさ where we only captured the kanji stem.
    if next_char and HIRAGANA_PATTERN.fullmatch(next_char):
        return True
    if prev_char and HIRAGANA_PATTERN.fullmatch(prev_char):
        return True

    return False


def _has_name_pattern(term: str) -> bool:
    compact_term = term.replace(" ", "")
    if _is_mixed_kanji_katakana(term):
        return (" " in term) or ("・" in term)
    if KATAKANA_ONLY_PATTERN.fullmatch(term):
        return (" " in term) or ("・" in term)
    if compact_term.endswith(NAME_LIKE_SUFFIXES):
        return True
    return False


def _is_dictionary_word(term: str) -> bool:
    tokenizer = _get_tokenizer()
    if tokenizer is None:
        return False

    compact_term = term.replace(" ", "").replace("・", "")
    if len(compact_term) < 2:
        return False

    tokens = list(tokenizer.tokenize(compact_term))
    if len(tokens) != 1:
        return False

    token = tokens[0]
    surface = getattr(token, "surface", "")
    if surface != compact_term:
        return False

    base_form = getattr(token, "base_form", "") or ""
    if base_form in {"", "*"}:
        return False

    return True


def _has_name_like_usage(term: str, sentences: list[str]) -> bool:
    return _has_name_pattern(term) or _has_honorific_context(term, sentences)


def _has_honorific_context(term: str, sentences: list[str]) -> bool:
    for sentence in sentences:
        for suffix in NAME_SUFFIXES:
            if f"{term}{suffix}" in sentence or f"{term} {suffix}" in sentence:
                return True
    return False


def _has_cooccurring_proper_noun(term: str, sentences: list[str], stop_terms: set[str]) -> bool:
    for sentence in sentences:
        found_terms: set[str] = set()
        for match in TERM_PATTERN.finditer(sentence):
            candidate = _normalize_term(match.group(0))
            if candidate == term or not _is_valid_term(candidate, stop_terms):
                continue
            found_terms.add(candidate)
        if found_terms:
            return True
    return False


def _score_term(
    term: str,
    count: int,
    file_count: int,
    sentences: list[str],
    stop_terms: set[str],
) -> float:
    score = 0.0
    score += min(count, 8) * 0.35
    score += min(file_count, 6) * 1.2
    if _is_mixed_kanji_katakana(term):
        score -= 1.0

    if _has_name_pattern(term):
        score += 1.4
    if _has_honorific_context(term, sentences):
        score += 2.2
    if _has_cooccurring_proper_noun(term, sentences, stop_terms):
        score += 1.2

    compact_term = term.replace(" ", "")
    if re.fullmatch(r"[\u4e00-\u9faf]+", compact_term) and len(compact_term) >= 2:
        score += 0.8
    if " " in term:
        score += 0.6

    return score


def _split_sentences(text: str) -> list[str]:
    raw_sentences = SENTENCE_SPLIT_PATTERN.split(text)
    sentences: list[str] = []
    for raw_sentence in raw_sentences:
        sentence = _normalize_sentence(raw_sentence)
        if sentence:
            sentences.append(sentence)
    return sentences


def _shorten_sentence_around_term(sentence: str, term: str) -> str:
    if len(sentence) <= MAX_SENTENCE_LENGTH:
        return sentence

    term_index = sentence.find(term)
    if term_index < 0:
        return sentence[:MAX_SENTENCE_LENGTH].rstrip() + "..."

    side_width = max((MAX_SENTENCE_LENGTH - len(term)) // 2, 16)
    start = max(term_index - side_width, 0)
    end = min(term_index + len(term) + side_width, len(sentence))
    clipped = sentence[start:end].strip()

    if start > 0:
        clipped = "..." + clipped
    if end < len(sentence):
        clipped = clipped + "..."
    return clipped


def _score_sentence(sentence: str, term: str) -> float:
    if term not in sentence:
        return float("-inf")

    length = len(sentence)
    if length < MIN_SENTENCE_LENGTH:
        return float("-inf")

    score = 100.0
    score -= abs(length - TARGET_SENTENCE_LENGTH) * 0.6

    if length > MAX_SENTENCE_LENGTH:
        score -= (length - MAX_SENTENCE_LENGTH) * 0.4

    if sentence.endswith(("。", "！", "？", "!", "?")):
        score += 8
    if "「" in sentence or "」" in sentence:
        score += 3
    if term == sentence:
        score -= 30
    if sentence.startswith(("え", "あ", "う", "お")) and length < 24:
        score -= 20

    term_count = sentence.count(term)
    if term_count > 1:
        score += 2

    return score


def _choose_example_sentence(term: str, sentences: list[str]) -> str:
    best_sentence = ""
    best_score = float("-inf")

    for sentence in sentences:
        score = _score_sentence(sentence, term)
        if score > best_score:
            best_score = score
            best_sentence = sentence

    if not best_sentence:
        return term
    return _shorten_sentence_around_term(best_sentence, term)


def _chunk_items(items: list[tuple[str, str]], batch_size: int) -> Iterable[list[tuple[str, str]]]:
    for start in range(0, len(items), batch_size):
        yield items[start : start + batch_size]


def _build_glossary_refine_prompt(novel_name: str, candidates: list[tuple[str, str]]) -> str:
    prompt_lines = GLOSSARY_SYSTEM_INSTRUCTIONS.copy()
    prompt_lines.append(f"Novel: {novel_name}")
    prompt_lines.append("Review the following candidate glossary entries.")
    prompt_lines.append("Each line is formatted as: Japanese term => example sentence")
    prompt_lines.append("<candidates>")
    for term, example in candidates:
        prompt_lines.append(f"{term} => {example}")
    prompt_lines.append("</candidates>")
    prompt_lines.append(
        'Return strict JSON like {"トレセン学園":"트레센 학원","セイウンスカイ":"세이운 스카이"} and include only accepted glossary entries.'
    )
    return "\n".join(prompt_lines)


def _extract_json_object(text: str) -> dict[str, str]:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end < start:
        raise ValueError("Model response did not contain a JSON object")

    payload = cleaned[start : end + 1]
    data = json.loads(payload)
    if not isinstance(data, dict):
        raise ValueError("Model response JSON must be an object")

    return {
        str(key).strip(): str(value).strip()
        for key, value in data.items()
        if str(key).strip() and str(value).strip()
    }


def _load_existing_glossary(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}

    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {}

    return {
        str(key).strip(): str(value).strip()
        for key, value in data.items()
        if str(key).strip() and str(value).strip()
    }


def _build_glossary_model_config() -> TranslationConfig:
    runtime_settings = get_runtime_settings()
    return TranslationConfig(
        source_file=None,
        server_executable=runtime_settings.llama_server_path,
        model_path=runtime_settings.llama_model_path,
        server_url=runtime_settings.server_url,
        glossary_path=runtime_settings.glossary_path,
        output_root=runtime_settings.output_root,
        max_chunk_chars=runtime_settings.max_chars,
        timeout=runtime_settings.timeout,
        draft_temperature=0.2,
        refine_temperature=runtime_settings.refine_temperature,
        refine_enabled=runtime_settings.refine_enabled,
        top_p=runtime_settings.top_p,
        n_predict=min(runtime_settings.n_predict, 2048),
        context_size=runtime_settings.ctx_size,
        gpu_layers=runtime_settings.gpu_layers,
        threads=runtime_settings.threads,
        sleep_seconds=0.0,
        startup_timeout=runtime_settings.startup_timeout,
    )


def refine_glossary_candidates(novel_dir: Path, candidates: dict[str, str], started_at: float) -> dict[str, str]:
    if not candidates:
        return {}

    config = _build_glossary_model_config()
    if config.server_executable is None or not config.server_executable.is_file():
        raise FileNotFoundError(f"llama-server executable not found: {config.server_executable}")
    if config.model_path is None or not config.model_path.is_file():
        raise FileNotFoundError(f"Model file not found: {config.model_path}")

    items = list(candidates.items())
    batches = list(_chunk_items(items, GLOSSARY_BATCH_SIZE))
    accepted: dict[str, str] = {}
    server_process = None

    try:
        render_glossary_refine_progress_screen(
            novel_name=novel_dir.name,
            batch_index=0,
            total_batches=len(batches),
            accepted_count=0,
            status_message="모델을 준비하는 중...",
        )
        server_process = start_llama_server(config)
        client = LlamaCppServerClient(config.server_url, config.timeout)
        client.wait_until_ready(config.startup_timeout)

        for batch_index, batch in enumerate(batches, start=1):
            status_message = f"{len(batch)}개 후보를 정제하는 중..."
            render_glossary_refine_progress_screen(
                novel_name=novel_dir.name,
                batch_index=batch_index,
                total_batches=len(batches),
                accepted_count=len(accepted),
                status_message=status_message,
            )
            prompt = _build_glossary_refine_prompt(novel_dir.name, batch)
            response = client.translate(
                prompt,
                temperature=0.1,
                top_p=config.top_p,
                n_predict=min(config.n_predict, 1536),
            )
            parsed = _extract_json_object(response)
            allowed_terms = {term for term, _ in batch}
            accepted.update({term: value for term, value in parsed.items() if term in allowed_terms})

        return dict(sorted(accepted.items(), key=lambda item: len(item[0]), reverse=True))
    finally:
        stop_llama_server(server_process)


def save_final_glossary(novel_dir: Path, glossary: dict[str, str]) -> Path:
    output_path = APP_ROOT / "glossary" / f"{novel_dir.name}.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    merged_glossary = _load_existing_glossary(output_path)
    for source, target in glossary.items():
        if source not in merged_glossary:
            merged_glossary[source] = target

    sorted_glossary = dict(sorted(merged_glossary.items(), key=lambda item: len(item[0]), reverse=True))
    output_path.write_text(json.dumps(sorted_glossary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output_path


def extract_glossary_candidates(novel_dir: Path) -> dict[str, str]:
    stop_terms = _load_stop_terms()
    chapter_files = find_chapter_files(novel_dir)
    term_counts: Counter[str] = Counter()
    file_counts: Counter[str] = Counter()
    sentences_by_term: dict[str, list[str]] = defaultdict(list)

    for chapter_file in chapter_files:
        document = parse_source_file(chapter_file)
        chapter_text = "\n".join([document.title, document.body])
        sentences = _split_sentences(chapter_text)
        seen_terms_in_file: set[str] = set()

        for match in TERM_PATTERN.finditer(chapter_text):
            term = _normalize_term(match.group(0))
            if not _is_valid_term(term, stop_terms):
                continue
            if _is_embedded_kanji_stem(chapter_text, match.start(), match.end(), term):
                continue
            term_counts[term] += 1
            seen_terms_in_file.add(term)

        for term in seen_terms_in_file:
            file_counts[term] += 1

        for sentence in sentences:
            found_terms: set[str] = set()
            for match in TERM_PATTERN.finditer(sentence):
                term = _normalize_term(match.group(0))
                if not _is_valid_term(term, stop_terms):
                    continue
                if _is_embedded_kanji_stem(sentence, match.start(), match.end(), term):
                    continue
                found_terms.add(term)

            for term in found_terms:
                sentences_by_term[term].append(sentence)

    candidates: dict[str, str] = {}
    for term, count in term_counts.most_common():
        if count < MIN_TERM_COUNT:
            continue
        if file_counts[term] < MIN_FILE_COUNT:
            continue
        sentences = sentences_by_term.get(term, [])
        if _is_dictionary_word(term) and not _has_name_like_usage(term, sentences):
            continue
        term_score = _score_term(term, count, file_counts[term], sentences, stop_terms)
        if term_score < MIN_TERM_SCORE:
            continue
        candidates[term] = _choose_example_sentence(term, sentences)
        if len(candidates) >= MAX_CANDIDATES:
            break

    return candidates


def main() -> int:
    runtime_settings = get_runtime_settings()
    source_root = runtime_settings.source_path
    novel_dirs = find_source_novels(source_root)
    if not novel_dirs:
        raise ValueError(f"No source novel folders found: {source_root}")

    status_message = None

    while True:
        render_glossary_selection_screen(
            source_root=source_root,
            novel_dirs=novel_dirs,
            status_message=status_message,
        )
        raw = input("").strip()
        command = parse_command(raw)

        if command in {"main", "back"}:
            return 0

        if not raw.isdigit():
            status_message = "[ERROR] 목록 번호를 입력해 주세요."
            continue

        selected_index = int(raw) - 1
        if not 0 <= selected_index < len(novel_dirs):
            status_message = "[ERROR] 목록에 있는 번호를 입력해 주세요."
            continue

        novel_dir = novel_dirs[selected_index]
        log_runtime_event(f"glossary candidate generation start | novel_dir={novel_dir}")
        render_glossary_candidate_progress_screen("후보 추출 중...")
        candidates = extract_glossary_candidates(novel_dir)
        glossary_started_at = time.monotonic()
        glossary = refine_glossary_candidates(novel_dir, candidates, glossary_started_at)
        output_path = save_final_glossary(novel_dir, glossary)
        log_runtime_event(
            f"glossary candidate generation complete | novel_dir={novel_dir} | "
            f"output_path={output_path} | candidates={len(candidates)} | glossary_entries={len(glossary)} | "
            f"elapsed_seconds={int(time.monotonic() - glossary_started_at)}"
        )

        render_glossary_complete_screen(
            output_path=output_path,
            candidate_count=len(_load_existing_glossary(output_path)),
            elapsed_seconds=int(time.monotonic() - glossary_started_at),
            status_message=None,
        )
        input("")
        return 0
