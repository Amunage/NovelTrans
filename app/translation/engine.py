from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from app.settings.prompt import SEPARATOR_LINE
from app.settings.prompt import with_user_prompt
from app.translation.language import get_translation_instructions, get_translation_language
from app.utils import SourceDocument, normalize_translation, print_progress, sanitize_model_text, split_into_chunks


@dataclass
class TranslationConfig:
    source_file: Path | None
    server_executable: Path | None
    model_path: Path | None
    server_url: str
    glossary_path: Path | None
    output_root: Path
    max_chunk_chars: int
    request_timeout: int
    draft_temperature: float
    refine_temperature: float
    refine_enabled: bool
    top_p: float
    max_tokens: int
    context_size: int
    gpu_layers: int | None
    threads: int | None
    sleep_seconds: float
    startup_timeout: int
    debug_mode: bool


class TranslatorClient(Protocol):
    def translate(
        self,
        prompt: str,
        *,
        temperature: float,
        top_p: float,
        max_tokens: int,
        wait_callback: Callable[[], None] | None = None,
    ) -> tuple[str, int]:
        ...


def validate_paths(config: TranslationConfig) -> None:
    if config.source_file is None:
        raise ValueError("Source file path is required")
    if config.server_executable is None:
        raise ValueError("llama-server executable path is required")
    if config.model_path is None:
        raise ValueError("Model path is required")

    if not config.source_file.is_file():
        raise FileNotFoundError(f"Source file not found: {config.source_file}")
    if not config.server_executable.is_file():
        raise FileNotFoundError(f"llama-server executable not found: {config.server_executable}")
    if not config.model_path.is_file():
        raise FileNotFoundError(f"Model file not found: {config.model_path}")


def load_glossary(glossary_path: Path | None) -> dict[str, str]:
    if glossary_path is None or not glossary_path.exists():
        return {}

    try:
        data = json.loads(glossary_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"Glossary file is not valid JSON: {glossary_path} ({exc})") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Glossary file must contain a JSON object: {glossary_path}")
    return {str(key): str(value) for key, value in data.items()}


def validate_glossary_file(glossary_path: Path | None) -> str | None:
    if glossary_path is None or not glossary_path.exists():
        return None

    try:
        load_glossary(glossary_path)
    except ValueError as exc:
        return f"[WARN] 용어집 JSON이 올바르지 않아 작업을 중단합니다: {exc}"

    return None


def filter_glossary_for_source(text: str | None, glossary: dict[str, str]) -> dict[str, str]:
    if not text or not glossary:
        return {}

    return {source: target for source, target in glossary.items() if source and source in text}


def build_prompts(
    current_source: str,
    previous_source: str | None,
    glossary: dict[str, str],
    *,
    is_title: bool,
) -> str:
    language = get_translation_language()
    prompt_lines = get_translation_instructions()
    tag_name = "title" if is_title else "current_source"
    current_source = sanitize_model_text(language.preprocess_source_text(current_source, is_title=is_title)) or ""
    previous_source = (
        sanitize_model_text(language.preprocess_source_text(previous_source, is_title=False))
        if previous_source is not None
        else None
    )
    prompt_glossary = filter_glossary_for_source(current_source, glossary)

    if is_title:
        prompt_lines.append(f"Translate the {language.source_label} title into concise, natural Korean.")

    if prompt_glossary:
        prompt_lines.append("If <glossary> is provided, preserve glossary-defined proper nouns and preferred terms exactly.")
        prompt_lines.append("<glossary>")
        for source, target in sorted(prompt_glossary.items(), key=lambda item: len(item[0]), reverse=True):
            prompt_lines.append(f"{source} => {target}")
        prompt_lines.append("</glossary>")

    if not is_title and previous_source:
        prompt_lines.append("If <previous_source> is provided, treat it as context only.")
        prompt_lines.extend(["<previous_source>", previous_source, "</previous_source>"])

    prompt_lines.append(f"Translate only the text inside <{tag_name}> into Korean.")
    prompt_lines.extend([f"<{tag_name}>", current_source, f"</{tag_name}>"])
    return "\n".join(with_user_prompt(prompt_lines, "translation_instructions"))


def build_output_path(source_file: Path, output_root: Path) -> Path:
    novel_name = source_file.parent.name or "unknown_novel"
    output_dir = output_root / novel_name
    return output_dir / f"{source_file.stem}_ko.txt"


def build_draft_output_path(source_file: Path, output_root: Path) -> Path:
    novel_name = source_file.parent.name or "unknown_novel"
    output_dir = output_root / novel_name / "draft"
    return output_dir / f"{source_file.stem}_ko_draft.txt"


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(content, encoding="utf-8")
    temp_path.replace(path)


def report_progress(
    *,
    progress_callback: Callable[[str, int, int, str | None], None] | None,
    fallback_stage: str,
    callback_stage: str,
    current: int,
    total: int,
    progress_label: str | None,
    status: str | None = None,
) -> None:
    if progress_callback is not None:
        progress_callback(callback_stage, current, total, status)
        return

    print_progress(fallback_stage, current, total, label=progress_label, status=status)


def build_translated_document(title: str, translated_chunks: list[str]) -> str:
    translated_body = "\n\n".join(chunk.strip() for chunk in translated_chunks if chunk.strip())
    return f"{title}\n{SEPARATOR_LINE}\n\n{translated_body}\n"


def build_debug_prompt_status(prompt: str) -> str:
    return f"[DEBUG] Final prompt\n{prompt}"


def translate_document(
    document: SourceDocument,
    client: TranslatorClient,
    config: TranslationConfig,
    *,
    progress_label: str | None = None,
    progress_callback: Callable[[str, int, int, str | None], None] | None = None,
    output_callback: Callable[[str, int, int, int, float, str | None], None] | None = None,
) -> tuple[str, list[str]]:
    glossary = load_glossary(config.glossary_path)
    body_chunks = split_into_chunks(document.body, config.max_chunk_chars)
    if not body_chunks:
        raise ValueError("No translatable paragraphs were found")

    all_chunks = [document.title, *body_chunks]

    translated_title = document.title
    translated_chunks: list[str] = []
    previous_source: str | None = None
    total_items = len(all_chunks)

    for index, chunk in enumerate(all_chunks, start=1):
        report_progress(
            progress_callback=progress_callback,
            fallback_stage="번역",
            callback_stage="초벌 번역",
            current=index - 1,
            total=total_items,
            progress_label=progress_label,
        )

        is_title = index == 1
        context_previous_source = previous_source if index >= 3 else None
        prompt = build_prompts(chunk, context_previous_source, glossary, is_title=is_title)
        debug_status = build_debug_prompt_status(prompt) if config.debug_mode else None
        if debug_status is not None and progress_callback is not None:
            progress_callback("초벌 번역", index - 1, total_items, debug_status)
        max_tokens = min(config.max_tokens, 256) if is_title else config.max_tokens

        chunk_started_at = time.monotonic()
        response, completion_tokens = client.translate(
            prompt,
            temperature=config.draft_temperature,
            top_p=config.top_p,
            max_tokens=max_tokens,
            wait_callback=(
                (lambda: progress_callback("초벌 번역", index - 1, total_items, debug_status))
                if progress_callback is not None
                else None
            ),
        )
        translated = normalize_translation(response)
        if not translated:
            raise RuntimeError(f"Empty translation received for chunk {index}")

        chunk_elapsed_seconds = max(time.monotonic() - chunk_started_at, 0.0)
        if output_callback is not None:
            output_callback("초벌 번역", index, total_items, completion_tokens, chunk_elapsed_seconds, debug_status)

        if index == 1:
            translated_title = translated or document.title
        else:
            translated_chunks.append(translated)
            previous_source = chunk

        if config.sleep_seconds > 0:
            time.sleep(config.sleep_seconds)

    report_progress(
        progress_callback=progress_callback,
        fallback_stage="번역",
        callback_stage="초벌 번역",
        current=total_items,
        total=total_items,
        progress_label=progress_label,
        status="초벌 번역 완료" if progress_callback is not None else "번역 완료",
    )

    return translated_title, translated_chunks
