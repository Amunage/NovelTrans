from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _get_app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


APP_ROOT = _get_app_root()


def _warn_invalid_env(name: str, value: str, default):
    print(f"[WARNING] .env 값이 잘못되어 기본값으로 대체합니다: {name}={value!r} -> {default!r}")


def _load_dotenv():
    env_path = APP_ROOT / ".env"
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _get_str(name: str, default: str) -> str:
    return os.getenv(name, default)


def _get_path(name: str, default: str) -> Path:
    raw_value = _get_str(name, default)
    path = Path(raw_value)
    if path.is_absolute():
        return path
    return APP_ROOT / path


def _get_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default

    try:
        return int(value)
    except ValueError:
        _warn_invalid_env(name, value, default)
        return default


def _get_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default

    try:
        return float(value)
    except ValueError:
        _warn_invalid_env(name, value, default)
        return default


def _get_json_list(name: str, default: list[str]) -> list[str]:
    value = os.getenv(name)
    if value is None:
        return default.copy()

    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        _warn_invalid_env(name, value, default)
        return default.copy()

    if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
        _warn_invalid_env(name, value, default)
        return default.copy()
    return parsed


_load_dotenv()

_DEFAULT_TRANSLATION_INSTRUCTIONS = [
    "You are a professional literary translator for Japanese web novels.",
    "Translate Japanese into faithful Korean that still reads naturally.",
    "Preserve meaning, tone, paragraph structure, and dialogue flow.",
    "Do not omit, summarize, simplify, or add information.",
    "Keep names, forms of address, and terminology consistent.",
    "Return only the Korean translation of the requested text.",
    "Do not add notes, labels, summaries, or quotation marks unless they exist in the source.",
    "Do not explain your reasoning.",
]

_DEFAULT_REFINER_INSTRUCTIONS = [
    "Rewrite this into natural Korean literary prose in a restrained, understated style.",
    "Do not intensify, embellish, or over-explain.",
]

LLAMA_SERVER_PATH = _get_path("LLAMA_SERVER_PATH", r"D:\llama.cpp\llama-server.exe")
LLAMA_MODEL_PATH = _get_path("LLAMA_MODEL_PATH", r"D:\llama.cpp\models\supergemma4\supergemma4-26b-uncensored-fast-v2-Q4_K_M.gguf")
GLOSSARY_PATH = _get_path("GLOSSARY_PATH", "glossary/umamusume.json")
SOURCE_PATH = _get_path("SOURCE_PATH", "source")
DEFAULT_SERVER_URL = _get_str("SERVER_URL", "http://127.0.0.1:8080")
DEFAULT_OUTPUT_ROOT = _get_path("OUTPUT_ROOT", "translated")
DEFAULT_MAX_CHARS = _get_int("MAX_CHARS", 1400)
DEFAULT_TIMEOUT = _get_int("TIMEOUT", 180)
DEFAULT_DRAFT_TEMPERATURE = _get_float("DRAFT_TEMPERATURE", 0.2)
DEFAULT_REFINE_TEMPERATURE = _get_float("REFINE_TEMPERATURE", 0.45)
DEFAULT_TOP_P = _get_float("TOP_P", 0.9)
DEFAULT_N_PREDICT = _get_int("N_PREDICT", 1800)
DEFAULT_CTX_SIZE = _get_int("CTX_SIZE", 8192)
DEFAULT_STARTUP_TIMEOUT = _get_int("STARTUP_TIMEOUT", 180)
SEPARATOR_LINE = _get_str("SEPARATOR_LINE", "=" * 60)
TRANSLATION_INSTRUCTIONS = _get_json_list("TRANSLATION_INSTRUCTIONS", _DEFAULT_TRANSLATION_INSTRUCTIONS)
REFINER_INSTRUCTIONS = _get_json_list("REFINER_INSTRUCTIONS", _DEFAULT_REFINER_INSTRUCTIONS)
