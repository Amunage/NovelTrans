from __future__ import annotations

import json

from app.settings.config import PROMPT_SETTINGS_PATH
from app.settings.default import DEFAULT_PROMPT_CONTENT, DEFAULT_SEPARATOR_LINE


def ensure_prompt_settings_file() -> None:
    PROMPT_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not PROMPT_SETTINGS_PATH.exists():
        PROMPT_SETTINGS_PATH.write_text(DEFAULT_PROMPT_CONTENT, encoding="utf-8")


def load_prompt_settings() -> tuple[str, str]:
    ensure_prompt_settings_file()
    data = json.loads(PROMPT_SETTINGS_PATH.read_text(encoding="utf-8"))

    translation_instructions = data.get("translation_instructions")
    refiner_instructions = data.get("refiner_instructions")

    if not isinstance(translation_instructions, str):
        raise ValueError(f"Invalid translation_instructions in {PROMPT_SETTINGS_PATH}")
    if not isinstance(refiner_instructions, str):
        raise ValueError(f"Invalid refiner_instructions in {PROMPT_SETTINGS_PATH}")

    return translation_instructions, refiner_instructions


SEPARATOR_LINE = DEFAULT_SEPARATOR_LINE
CUSTOM_TRANSLATION_INSTRUCTIONS, CUSTOM_REFINER_INSTRUCTIONS = load_prompt_settings()


__all__ = [
    "CUSTOM_REFINER_INSTRUCTIONS",
    "CUSTOM_TRANSLATION_INSTRUCTIONS",
    "SEPARATOR_LINE",
    "ensure_prompt_settings_file",
    "load_prompt_settings",
]