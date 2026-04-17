from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from app.settings import SEPARATOR_LINE, TRANSLATION_INSTRUCTIONS
from app.utils import SourceDocument, normalize_translation, print_progress, sanitize_model_text, split_into_chunks


INSTRUCTIONS = TRANSLATION_INSTRUCTIONS


@dataclass
class TranslationConfig:
    source_file: Path | None
    server_executable: Path | None
    model_path: Path | None
    server_url: str
    glossary_path: Path | None
    output_root: Path
    max_chunk_chars: int
    timeout: int
    draft_temperature: float
    refine_temperature: float
    top_p: float
    n_predict: int
    context_size: int
    gpu_layers: int | None
    threads: int | None
    sleep_seconds: float
    startup_timeout: int


class TranslatorClient(Protocol):
    def translate(self, prompt: str, *, temperature: float, top_p: float, n_predict: int) -> str:
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

    data = json.loads(glossary_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Glossary file must contain a JSON object: {glossary_path}")
    return {str(key): str(value) for key, value in data.items()}


def build_prompts(
    current_source: str,
    previous_source: str | None,
    previous_translation: str | None,
    glossary: dict[str, str],
    *,
    is_title: bool,
) -> str:
    prompt_lines = INSTRUCTIONS.copy()
    tag_name = "title" if is_title else "current_source"
    current_source = sanitize_model_text(current_source) or ""
    previous_source = sanitize_model_text(previous_source)
    previous_translation = sanitize_model_text(previous_translation)

    if is_title:
        prompt_lines.append("Translate the Japanese title into concise, natural Korean.")

    if glossary:
        prompt_lines.append("If <glossary> is provided, preserve glossary-defined proper nouns and preferred terms exactly.")
        prompt_lines.append("<glossary>")
        for source, target in sorted(glossary.items(), key=lambda item: len(item[0]), reverse=True):
            prompt_lines.append(f"{source} => {target}")
        prompt_lines.append("</glossary>")

    if not is_title and previous_source and previous_translation:
        prompt_lines.append("If <previous_source> and <previous_translation> are provided, treat them as context only.")
        prompt_lines.extend(["<previous_source>", previous_source, "</previous_source>"])
        prompt_lines.extend(["<previous_translation>", previous_translation, "</previous_translation>"])

    prompt_lines.append(f"Translate only the text inside <{tag_name}> into Korean.")
    prompt_lines.extend([f"<{tag_name}>", current_source, f"</{tag_name}>"])
    return "\n".join(prompt_lines)


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


def translate_document(
    document: SourceDocument,
    client: TranslatorClient,
    config: TranslationConfig,
    draft_output_path: Path,
    *,
    progress_label: str | None = None,
    progress_callback: Callable[[str, int, int, str | None], None] | None = None,
) -> tuple[str, list[str]]:
    glossary = load_glossary(config.glossary_path)
    body_chunks = split_into_chunks(document.body, config.max_chunk_chars)
    if not body_chunks:
        raise ValueError("No translatable paragraphs were found")

    all_chunks = [document.title, *body_chunks]

    translated_title = document.title
    translated_chunks: list[str] = []
    previous_source: str | None = None
    previous_translation: str | None = None
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
        context_previous_translation = previous_translation if index >= 3 else None
        prompt = build_prompts(
            chunk,
            context_previous_source,
            context_previous_translation,
            glossary,
            is_title=is_title,
        )
        max_tokens = min(config.n_predict, 256) if is_title else config.n_predict

        response = client.translate(
            prompt,
            temperature=config.draft_temperature,
            top_p=config.top_p,
            n_predict=max_tokens,
        )
        translated = normalize_translation(response)
        if not translated:
            raise RuntimeError(f"Empty translation received for chunk {index}")

        if index == 1:
            translated_title = translated or document.title
        else:
            translated_chunks.append(translated)
            previous_source = chunk
            previous_translation = translated

        atomic_write_text(draft_output_path, build_translated_document(translated_title, translated_chunks))

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
