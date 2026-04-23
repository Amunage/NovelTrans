from __future__ import annotations

from pathlib import Path
from typing import Callable

from app.translation.engine import (
    TranslationConfig,
    TranslatorClient,
    atomic_write_text,
    build_translated_document,
    load_glossary,
    report_progress,
)
from app.translation.language import get_refiner_instructions, get_translation_language
from app.utils import normalize_translation, sanitize_model_text


def build_refine_prompts(
    current_text: str,
    previous_source: str | None,
    glossary: dict[str, str],
    *,
    is_title: bool,
) -> str:
    language = get_translation_language()
    prompt_lines = get_refiner_instructions()
    tag_name = "title" if is_title else "current_text"
    current_text = sanitize_model_text(language.preprocess_refine_text(current_text, is_title=is_title)) or ""
    previous_source = (
        sanitize_model_text(language.preprocess_source_text(previous_source, is_title=False))
        if previous_source is not None
        else None
    )

    if is_title:
        prompt_lines.append("Return exactly one revised title only.")
    else:
        prompt_lines.append("Return only the revised Korean text.")

    if glossary:
        prompt_lines.append("If <glossary> is provided, keep those Korean proper nouns and fixed terms exactly as written.")
        prompt_lines.append("<glossary>")
        for _, target in glossary.items():
            prompt_lines.append(target)
        prompt_lines.append("</glossary>")

    if not is_title and previous_source:
        prompt_lines.append("If <previous_source> is provided, treat it as context only.")
        prompt_lines.extend(["<previous_source>", previous_source, "</previous_source>"])

    prompt_lines.append(f"Rewrite only the text inside <{tag_name}>.")
    prompt_lines.extend([f"<{tag_name}>", current_text, f"</{tag_name}>"])
    return "\n".join(prompt_lines)


def _refine_once(
    current_text: str,
    previous_source: str | None,
    glossary: dict[str, str],
    client: TranslatorClient,
    config: TranslationConfig,
    *,
    is_title: bool,
    item_index: int,
    total_items: int,
    progress_callback: Callable[[str, int, int, str | None], None] | None = None,
) -> str:
    prompt = build_refine_prompts(current_text, previous_source, glossary, is_title=is_title)
    base_temperature = config.refine_temperature
    temperature = min(max(base_temperature, 0.35), 0.8)
    top_p = max(config.top_p, 0.95)
    max_tokens = min(config.n_predict, 256) if is_title else config.n_predict
    return client.translate(
        prompt,
        temperature=temperature,
        top_p=top_p,
        n_predict=max_tokens,
        wait_callback=(
            (lambda: progress_callback("다듬기", item_index - 1, total_items, None))
            if progress_callback is not None
            else None
        ),
    )


def refine_document(
    translated_title: str,
    translated_chunks: list[str],
    source_chunks: list[str],
    client: TranslatorClient,
    config: TranslationConfig,
    output_path: Path,
    *,
    progress_label: str | None = None,
    progress_callback: Callable[[str, int, int, str | None], None] | None = None,
) -> tuple[str, list[str]]:
    if len(source_chunks) != len(translated_chunks):
        raise ValueError("Source chunk count does not match translated chunk count for refinement")

    glossary = load_glossary(config.glossary_path)
    total_items = len(translated_chunks) + 1

    report_progress(
        progress_callback=progress_callback,
        fallback_stage="다듬기",
        callback_stage="다듬기",
        current=0,
        total=total_items,
        progress_label=progress_label,
    )
    refined_title = _refine_once(
        translated_title,
        None,
        glossary,
        is_title=True,
        client=client,
        config=config,
        item_index=1,
        total_items=total_items,
        progress_callback=progress_callback,
    )
    refined_title = normalize_translation(refined_title)
    refined_title = refined_title or translated_title

    refined_chunks: list[str] = []

    for index, chunk in enumerate(translated_chunks, start=2):
        report_progress(
            progress_callback=progress_callback,
            fallback_stage="다듬기",
            callback_stage="다듬기",
            current=index - 1,
            total=total_items,
            progress_label=progress_label,
        )
        source_index = index - 2
        previous_source = source_chunks[source_index - 1] if source_index >= 1 else None
        refined_chunk = _refine_once(
            chunk,
            previous_source,
            glossary,
            is_title=False,
            client=client,
            config=config,
            item_index=index,
            total_items=total_items,
            progress_callback=progress_callback,
        )

        refined_chunk = normalize_translation(refined_chunk)
        refined_chunk = refined_chunk or chunk
        refined_chunks.append(refined_chunk)
        atomic_write_text(output_path, build_translated_document(refined_title, refined_chunks))

    report_progress(
        progress_callback=progress_callback,
        fallback_stage="다듬기",
        callback_stage="다듬기",
        current=total_items,
        total=total_items,
        progress_label=progress_label,
        status="다듬기 완료",
    )

    return refined_title, refined_chunks