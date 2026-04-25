from __future__ import annotations

import json

from app.settings.config import PROMPT_SETTINGS_PATH
from app.settings.default import DEFAULT_PROMPT_CONTENT, DEFAULT_SEPARATOR_LINE

PROMPT_KEYS = ("translation_instructions", "refiner_instructions", "glossary_instructions")


def ensure_prompt_settings_file() -> None:
    PROMPT_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not PROMPT_SETTINGS_PATH.exists():
        PROMPT_SETTINGS_PATH.write_text(DEFAULT_PROMPT_CONTENT, encoding="utf-8")


def load_prompt_settings() -> dict[str, str]:
    ensure_prompt_settings_file()
    try:
        data = json.loads(PROMPT_SETTINGS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid prompt settings JSON in {PROMPT_SETTINGS_PATH}: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError(f"Prompt settings must contain a JSON object: {PROMPT_SETTINGS_PATH}")

    settings: dict[str, str] = {}
    needs_repair = False
    for key in PROMPT_KEYS:
        value = data.get(key, "")
        if not isinstance(value, str):
            raise ValueError(f"Invalid {key} in {PROMPT_SETTINGS_PATH}")
        settings[key] = value
        if key not in data:
            needs_repair = True

    if needs_repair:
        save_prompt_settings(settings)

    return settings


def save_prompt_settings(settings: dict[str, str]) -> None:
    ensure_prompt_settings_file()
    data = {key: str(settings.get(key, "")) for key in PROMPT_KEYS}
    PROMPT_SETTINGS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def get_user_prompt(key: str) -> str:
    if key not in PROMPT_KEYS:
        raise ValueError(f"Unsupported prompt key: {key}")
    return load_prompt_settings().get(key, "").strip()


def with_user_prompt(prompt_lines: list[str], key: str) -> list[str]:
    user_prompt = get_user_prompt(key)
    if not user_prompt:
        return prompt_lines
    return ["<user_prompt>", user_prompt, "</user_prompt>", *prompt_lines]


SEPARATOR_LINE = DEFAULT_SEPARATOR_LINE


__all__ = [
    "PROMPT_KEYS",
    "SEPARATOR_LINE",
    "ensure_prompt_settings_file",
    "get_user_prompt",
    "load_prompt_settings",
    "save_prompt_settings",
    "with_user_prompt",
]
