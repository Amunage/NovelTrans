from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from app.server.llama import LlamaCppServerClient, start_llama_server, stop_llama_server
from app.settings.config import DATA_ROOT, get_runtime_settings
from app.settings.downloads import DownloadCancelledError, download_file
from app.settings.logging import log_runtime_event
from app.terms.wordlist import (
    clear_wordlist_cache,
    get_language_wordlist_filename,
    get_wordlist_download_url,
    get_wordlist_path,
)
from app.ui.control import (
    parse_command,
    prompt_glossary_min_term_count,
    prompt_glossary_novel_choice,
    wait_for_enter,
)
from app.translation.engine import TranslationConfig
from app.ui import (
    render_download_progress_screen,
    render_glossary_candidate_progress_screen,
    render_glossary_complete_screen,
    render_glossary_refine_progress_screen,
)
from app.utils import find_source_novels


GLOSSARY_BATCH_SIZE = 30
MIN_SENTENCE_LENGTH = 12
MAX_SENTENCE_LENGTH = 140
TARGET_SENTENCE_LENGTH = 48
SENTENCE_SPLIT_PATTERN = re.compile(r"(?<=[。！？.!?])\s+|\n+")


@dataclass(frozen=True)
class GlossaryLanguageSupport:
    key: str
    source_label: str
    extract_glossary_candidates: Callable[[Path, int], dict[str, str]]
    build_refine_prompt: Callable[[str, list[tuple[str, str]]], str]
    default_min_term_count: int = 5


def _normalize_sentence(sentence: str) -> str:
    normalized = sentence.replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


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
    if any(quote in sentence for quote in ("「", "」", "“", "”", '"')):
        score += 3
    if term == sentence:
        score -= 30
    if sentence.count(term) > 1:
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


def ensure_language_wordlist(language_key: str) -> str | None:
    filename = get_language_wordlist_filename(language_key)
    if filename is None:
        return None

    destination = get_wordlist_path(filename)
    if destination.is_file():
        return None

    download_url = get_wordlist_download_url(filename)
    log_runtime_event(f"wordlist download start | language={language_key} | destination={destination}")
    try:
        download_file(
            download_url,
            destination,
            filename,
            1,
            1,
            request_headers={"User-Agent": "noveltrans-wordlist-downloader"},
            render_progress=lambda asset_name, percent, speed_mbps: render_download_progress_screen(
                title="Dictionary download",
                message=f"Downloading {language_key} glossary dictionary...",
                item_label="Dictionary",
                item_name=asset_name,
                destination_path=str(destination),
                percent=max(0, min(percent, 100)),
                speed_mbps=speed_mbps,
            ),
        )
    except DownloadCancelledError as exc:
        log_runtime_event(f"wordlist download cancelled | language={language_key} | asset={exc.asset_name}")
        return "[WARN] 사전 다운로드를 취소했습니다. 사전 필터 없이 진행합니다."
    except Exception as exc:
        log_runtime_event(f"wordlist download failed | language={language_key} | error={exc!r}")
        return f"[WARN] 사전 다운로드에 실패했습니다. 사전 필터 없이 진행합니다: {exc}"

    clear_wordlist_cache()
    log_runtime_event(f"wordlist download complete | language={language_key} | destination={destination}")
    return None


def _prompt_min_term_count(default_count: int) -> int | None:
    status_message: str | None = None

    while True:
        raw = prompt_glossary_min_term_count(
            default_count=default_count,
            status_message=status_message,
        )
        command = parse_command(raw)
        if command == "back":
            return None
        if raw == "":
            return default_count

        try:
            value = int(raw)
        except ValueError:
            status_message = "[ERROR] 1 이상의 정수를 입력해 주세요."
            continue

        if value < 1:
            status_message = "[ERROR] 1 이상의 정수를 입력해 주세요."
            continue

        return value


def refine_glossary_candidates(
    novel_dir: Path,
    candidates: dict[str, str],
    language: GlossaryLanguageSupport,
) -> dict[str, str]:
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
            render_glossary_refine_progress_screen(
                novel_name=novel_dir.name,
                batch_index=batch_index,
                total_batches=len(batches),
                accepted_count=len(accepted),
                status_message=f"{len(batch)}개 후보를 정제하는 중...",
            )
            response = client.translate(
                language.build_refine_prompt(novel_dir.name, batch),
                temperature=0.1,
                top_p=config.top_p,
                n_predict=min(config.n_predict, 1536),
            )
            parsed = _extract_json_object(response)
            for term, _ in batch:
                if term in parsed:
                    accepted[term] = parsed[term]

        return accepted
    finally:
        stop_llama_server(server_process)


def save_final_glossary(novel_dir: Path, glossary: dict[str, str]) -> Path:
    output_path = DATA_ROOT / "glossary" / f"{novel_dir.name}.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    existing_glossary = _load_existing_glossary(output_path)
    merged_glossary: dict[str, str] = {}
    for source, target in glossary.items():
        merged_glossary[source] = existing_glossary.get(source, target)
    for source, target in existing_glossary.items():
        if source not in merged_glossary:
            merged_glossary[source] = target

    output_path.write_text(json.dumps(merged_glossary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output_path


def run_glossary_workflow(language: GlossaryLanguageSupport) -> int:
    runtime_settings = get_runtime_settings()
    source_root = runtime_settings.source_path
    novel_dirs = find_source_novels(source_root)
    if not novel_dirs:
        raise ValueError(f"No source novel folders found: {source_root}")

    render_glossary_candidate_progress_screen("사전 파일을 확인하는 중...")
    status_message = ensure_language_wordlist(language.key)
    min_term_count = _prompt_min_term_count(language.default_min_term_count)
    if min_term_count is None:
        return 0

    min_count_message = f"[INFO] 최소 출현 횟수: {min_term_count}"
    status_message = f"{status_message}\n{min_count_message}" if status_message else min_count_message

    while True:
        raw = prompt_glossary_novel_choice(
            source_root=source_root,
            novel_dirs=novel_dirs,
            target_lang=language.key,
            status_message=status_message,
        )
        command = parse_command(raw)

        if command == "back":
            return 0

        if not raw.isdigit():
            status_message = "[ERROR] 목록 번호를 입력해 주세요."
            continue

        selected_index = int(raw) - 1
        if not 0 <= selected_index < len(novel_dirs):
            status_message = "[ERROR] 목록에 있는 번호를 입력해 주세요."
            continue

        novel_dir = novel_dirs[selected_index]
        log_runtime_event(
            f"glossary candidate generation start | novel_dir={novel_dir} | target_lang={language.key} | "
            f"min_term_count={min_term_count}"
        )
        render_glossary_candidate_progress_screen("후보 추출 중...")
        candidates = language.extract_glossary_candidates(novel_dir, min_term_count)
        glossary_started_at = time.monotonic()
        glossary = refine_glossary_candidates(novel_dir, candidates, language)
        output_path = save_final_glossary(novel_dir, glossary)
        elapsed_seconds = int(time.monotonic() - glossary_started_at)
        log_runtime_event(
            f"glossary candidate generation complete | novel_dir={novel_dir} | target_lang={language.key} | "
            f"output_path={output_path} | candidates={len(candidates)} | glossary_entries={len(glossary)} | "
            f"elapsed_seconds={elapsed_seconds}"
        )

        render_glossary_complete_screen(
            output_path=output_path,
            candidate_count=len(_load_existing_glossary(output_path)),
            elapsed_seconds=elapsed_seconds,
            status_message=None,
        )
        wait_for_enter()
        return 0


__all__ = [
    "GlossaryLanguageSupport",
    "GLOSSARY_BATCH_SIZE",
    "MAX_SENTENCE_LENGTH",
    "TARGET_SENTENCE_LENGTH",
    "_choose_example_sentence",
    "_normalize_sentence",
    "_split_sentences",
    "refine_glossary_candidates",
    "run_glossary_workflow",
    "save_final_glossary",
]
