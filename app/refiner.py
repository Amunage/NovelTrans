from __future__ import annotations

from pathlib import Path
from typing import Callable

from app.settings import REFINER_INSTRUCTIONS
from app.translation import (
    TranslationConfig,
    TranslatorClient,
    atomic_write_text,
    build_translated_document,
    load_glossary,
    report_progress,
)
from app.utils import normalize_translation, sanitize_model_text


INSTRUCTIONS = REFINER_INSTRUCTIONS


def build_refine_prompts(
    current_text: str,
    glossary: dict[str, str],
    *,
    is_title: bool,
) -> str:
    prompt_lines = INSTRUCTIONS.copy()
    tag_name = "title" if is_title else "current_text"
    current_text = sanitize_model_text(current_text) or ""

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

    prompt_lines.append(f"Rewrite only the text inside <{tag_name}>.")
    prompt_lines.extend([f"<{tag_name}>", current_text, f"</{tag_name}>"])
    return "\n".join(prompt_lines)


def _refine_once(
    current_text: str,
    glossary: dict[str, str],
    client: TranslatorClient,
    config: TranslationConfig,
    *,
    is_title: bool,
) -> str:
    prompt = build_refine_prompts(current_text, glossary, is_title=is_title)
    base_temperature = config.refine_temperature
    temperature = min(max(base_temperature, 0.35), 0.8)
    top_p = max(config.top_p, 0.95)
    max_tokens = min(config.n_predict, 256) if is_title else config.n_predict
    return client.translate(
        prompt,
        temperature=temperature,
        top_p=top_p,
        n_predict=max_tokens,
    )


def refine_document(
    translated_title: str,
    translated_chunks: list[str],
    client: TranslatorClient,
    config: TranslationConfig,
    output_path: Path,
    *,
    progress_label: str | None = None,
    progress_callback: Callable[[str, int, int, str | None], None] | None = None,
) -> tuple[str, list[str]]:
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
        glossary,
        is_title=True,
        client=client,
        config=config,
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
        refined_chunk = _refine_once(
            chunk,
            glossary,
            is_title=False,
            client=client,
            config=config,
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
