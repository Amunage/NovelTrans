from __future__ import annotations

import time
from typing import Callable

from app.settings.prompt import with_user_prompt
from app.translation.engine import (
    TranslationConfig,
    TranslatorClient,
    build_debug_prompt_status,
    load_glossary,
    report_progress,
)
from app.translation.language import get_refiner_instructions, get_translation_language
from app.utils import normalize_translation, sanitize_model_text


def filter_glossary_for_translation(text: str | None, glossary: dict[str, str]) -> dict[str, str]:
    if not text or not glossary:
        return {}

    return {source: target for source, target in glossary.items() if target and target in text}


def build_refine_prompts(
    current_text: str,
    current_source: str | None,
    glossary: dict[str, str],
    *,
    is_title: bool,
) -> str:
    language = get_translation_language()
    prompt_lines = get_refiner_instructions()
    tag_name = "title" if is_title else "current_text"
    current_text = sanitize_model_text(language.preprocess_refine_text(current_text, is_title=is_title)) or ""
    current_source = (
        sanitize_model_text(language.preprocess_source_text(current_source, is_title=False))
        if current_source is not None
        else None
    )
    prompt_glossary = filter_glossary_for_translation(current_text, glossary)

    if is_title:
        prompt_lines.append("Return exactly one revised title only.")
    else:
        prompt_lines.append("Return only the revised Korean text.")

    if prompt_glossary:
        prompt_lines.append("If <glossary> is provided, keep those Korean proper nouns and fixed terms exactly as written.")
        prompt_lines.append("<glossary>")
        for _, target in prompt_glossary.items():
            prompt_lines.append(target)
        prompt_lines.append("</glossary>")

    if not is_title and current_source:
        prompt_lines.append("Use <current_source> as the source text for checking meaning, tone, and line alignment.")
        prompt_lines.extend(["<current_source>", current_source, "</current_source>"])

    prompt_lines.append(f"Rewrite only the text inside <{tag_name}>.")
    prompt_lines.extend([f"<{tag_name}>", current_text, f"</{tag_name}>"])
    return "\n".join(with_user_prompt(prompt_lines, "refiner_instructions"))


def _refine_once(
    current_text: str,
    current_source: str | None,
    glossary: dict[str, str],
    client: TranslatorClient,
    config: TranslationConfig,
    *,
    is_title: bool,
    item_index: int,
    total_items: int,
    progress_callback: Callable[[str, int, int, str | None], None] | None = None,
) -> tuple[str, int, float]:
    prompt = build_refine_prompts(current_text, current_source, glossary, is_title=is_title)
    debug_status = build_debug_prompt_status(prompt) if config.debug_mode else None
    if debug_status is not None and progress_callback is not None:
        progress_callback("다듬기", item_index - 1, total_items, debug_status)
    base_temperature = config.refine_temperature
    temperature = min(max(base_temperature, 0.35), 0.8)
    top_p = max(config.top_p, 0.95)
    max_tokens = min(config.max_tokens, 256) if is_title else config.max_tokens
    chunk_started_at = time.monotonic()
    response, completion_tokens = client.translate(
        prompt,
        temperature=temperature,
        top_p=top_p,
        max_tokens=max_tokens,
        wait_callback=(
            (lambda: progress_callback("다듬기", item_index - 1, total_items, debug_status))
            if progress_callback is not None
            else None
        ),
    )
    return response, completion_tokens, max(time.monotonic() - chunk_started_at, 0.0)


def refine_document(
    translated_title: str,
    translated_chunks: list[str],
    source_chunks: list[str | None],
    client: TranslatorClient,
    config: TranslationConfig,
    *,
    progress_label: str | None = None,
    progress_callback: Callable[[str, int, int, str | None], None] | None = None,
    output_callback: Callable[[str, int, int, int, float, str | None], None] | None = None,
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
    refined_title, refined_title_tokens, refined_title_elapsed = _refine_once(
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
    if output_callback is not None:
        output_callback("다듬기", 1, total_items, refined_title_tokens, refined_title_elapsed, None)

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
        current_source = source_chunks[source_index]
        refined_chunk, refined_chunk_tokens, refined_chunk_elapsed = _refine_once(
            chunk,
            current_source,
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
        if output_callback is not None:
            output_callback("다듬기", index, total_items, refined_chunk_tokens, refined_chunk_elapsed, None)

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
