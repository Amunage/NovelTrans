from __future__ import annotations

import ctypes
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

def _get_windows_executable_dir() -> Path | None:
    if os.name != "nt":
        return None

    buffer_size = 32768
    buffer = ctypes.create_unicode_buffer(buffer_size)
    length = ctypes.windll.kernel32.GetModuleFileNameW(None, buffer, buffer_size)
    if length <= 0:
        return None
    return Path(buffer.value).resolve().parent


def get_app_root() -> Path:
    if getattr(sys, "frozen", False):
        windows_executable_dir = _get_windows_executable_dir()
        if windows_executable_dir is not None and windows_executable_dir.exists():
            return windows_executable_dir

        argv0 = Path(sys.argv[0]).resolve().parent if sys.argv and sys.argv[0] else None
        executable_dir = Path(sys.executable).resolve().parent
        if argv0 is not None and argv0.exists():
            return argv0
        return executable_dir
    return Path(__file__).resolve().parents[1]


APP_ROOT = get_app_root()
PROMPT_SETTINGS_PATH = APP_ROOT / "datas" / "prompt.json"
DATA_DIR = APP_ROOT / "datas"
LOG_FILE_NAME = "app.log"
_LOGGING_INITIALIZED = False
DEFAULT_MODEL_FILENAME = "gemma-4-26B-A4B-it-UD-Q4_K_M.gguf"
EDITABLE_ENV_KEYS = [
    "LLAMA_SERVER_PATH",
    "LLAMA_MODEL_PATH",
    "GLOSSARY_PATH",
    "SOURCE_PATH",
    "OUTPUT_ROOT",
    "SERVER_URL",
    "MAX_CHARS",
    "TIMEOUT",
    "DRAFT_TEMPERATURE",
    "REFINE_TEMPERATURE",
    "REFINE_ENABLED",
    "TOP_P",
    "N_PREDICT",
    "CTX_SIZE",
    "STARTUP_TIMEOUT",
]
DEFAULT_ENV_VALUES = {
    "LLAMA_SERVER_PATH": "llama/llama-server.exe",
    "LLAMA_MODEL_PATH": f"models/{DEFAULT_MODEL_FILENAME}",
    "GLOSSARY_PATH": "glossary/glossary.json",
    "SOURCE_PATH": "source",
    "OUTPUT_ROOT": "translated",
    "SERVER_URL": "http://127.0.0.1:8080",
    "MAX_CHARS": "1400",
    "TIMEOUT": "180",
    "DRAFT_TEMPERATURE": "0.2",
    "REFINE_TEMPERATURE": "0.7",
    "REFINE_ENABLED": "on",
    "TOP_P": "0.9",
    "N_PREDICT": "1800",
    "CTX_SIZE": "8192",
    "STARTUP_TIMEOUT": "180",
}
DEFAULT_ENV_CONTENT = "\n".join(f"{key}={value}" for key, value in DEFAULT_ENV_VALUES.items()) + "\n"
DEFAULT_GLOSSARY_CONTENT = """{
 "ウマ娘": "우마무스메",
 "トレセン": "트레센"
}
"""


DEFAULT_PROMPT_SETTINGS = {
    "separator_line": "=" * 60,
    "translation_instructions": [
        "You are a professional literary translator for web novels.",
        "Translate the source text into faithful Korean that still reads naturally.",
        "Preserve meaning, tone, paragraph structure, and dialogue flow.",
        "Do not omit, summarize, simplify, or add information.",
        "Keep names, forms of address, and terminology consistent.",
        "Return only the Korean translation of the requested text.",
        "Do not add notes, labels, summaries, or quotation marks unless they exist in the source.",
        "Do not explain your reasoning.",
    ],
    "refiner_instructions": [
        "Rewrite this into natural Korean literary prose in a restrained, understated style.",
        "Do not intensify, embellish, or over-explain.",
    ],
}
DEFAULT_PROMPT_CONTENT = json.dumps(DEFAULT_PROMPT_SETTINGS, ensure_ascii=False, indent=2) + "\n"


def _load_prompt_settings() -> tuple[str, list[str], list[str]]:
    data = json.loads(PROMPT_SETTINGS_PATH.read_text(encoding="utf-8"))

    separator_line = data.get("separator_line")
    translation_instructions = data.get("translation_instructions")
    refiner_instructions = data.get("refiner_instructions")

    if not isinstance(separator_line, str) or not separator_line:
        raise ValueError(f"Invalid separator_line in {PROMPT_SETTINGS_PATH}")
    if not isinstance(translation_instructions, list) or not all(
        isinstance(item, str) for item in translation_instructions
    ):
        raise ValueError(f"Invalid translation_instructions in {PROMPT_SETTINGS_PATH}")
    if not isinstance(refiner_instructions, list) or not all(isinstance(item, str) for item in refiner_instructions):
        raise ValueError(f"Invalid refiner_instructions in {PROMPT_SETTINGS_PATH}")

    return separator_line, translation_instructions, refiner_instructions


SEPARATOR_LINE, TRANSLATION_INSTRUCTIONS, REFINER_INSTRUCTIONS = _load_prompt_settings()
TRANSLATION_INSTRUCTIONS = TRANSLATION_INSTRUCTIONS.copy()
REFINER_INSTRUCTIONS = REFINER_INSTRUCTIONS.copy()


@dataclass(frozen=True)
class RuntimeSettings:
    llama_server_path: Path
    llama_model_path: Path
    glossary_path: Path
    source_path: Path
    server_url: str
    output_root: Path
    max_chars: int
    timeout: int
    draft_temperature: float
    refine_temperature: float
    refine_enabled: bool
    top_p: float
    n_predict: int
    ctx_size: int
    startup_timeout: int


def get_log_path() -> Path:
    primary_path = DATA_DIR / LOG_FILE_NAME
    try:
        primary_path.parent.mkdir(parents=True, exist_ok=True)
        return primary_path
    except OSError:
        fallback_dir = Path(tempfile.gettempdir()) / "noveltrans"
        fallback_dir.mkdir(parents=True, exist_ok=True)
        return fallback_dir / LOG_FILE_NAME


def log_runtime_event(message: str) -> None:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_path = get_log_path()
    try:
        with log_path.open("a", encoding="utf-8") as log_file:
            log_file.write(f"[{timestamp}] {message}\n")
    except OSError:
        pass


def initialize_runtime_logging() -> None:
    global _LOGGING_INITIALIZED

    if _LOGGING_INITIALIZED:
        return

    log_path = get_log_path()
    try:
        log_path.write_text("", encoding="utf-8")
    except OSError:
        pass

    _LOGGING_INITIALIZED = True


def _warn_invalid_env(name: str, value: str, default: object) -> None:
    print(f"[WARNING] .env value is invalid, using default: {name}={value!r} -> {default!r}")


def load_dotenv() -> None:
    env_path = APP_ROOT / ".env"
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        os.environ[key.strip()] = value.strip().strip('"').strip("'")


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


def _get_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default

    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False

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


def read_env_file(env_path: Path | None = None) -> dict[str, str]:
    resolved_env_path = env_path or (APP_ROOT / ".env")
    if not resolved_env_path.exists():
        return {}

    values: dict[str, str] = {}
    for raw_line in resolved_env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def update_env_value(key: str, value: str, env_path: Path | None = None) -> None:
    resolved_env_path = env_path or (APP_ROOT / ".env")
    lines = resolved_env_path.read_text(encoding="utf-8").splitlines() if resolved_env_path.exists() else []
    updated_lines: list[str] = []
    replaced = False

    for raw_line in lines:
        stripped = raw_line.strip()
        if stripped and not stripped.startswith("#") and "=" in raw_line:
            existing_key, _ = raw_line.split("=", 1)
            if existing_key.strip() == key:
                updated_lines.append(f"{key}={value}")
                replaced = True
                continue
        updated_lines.append(raw_line)

    if not replaced:
        updated_lines.append(f"{key}={value}")

    resolved_env_path.write_text("\n".join(updated_lines).rstrip() + "\n", encoding="utf-8")


def get_env_settings_items() -> list[tuple[str, str]]:
    env_values = read_env_file()
    return [(key, env_values.get(key, DEFAULT_ENV_VALUES.get(key, ""))) for key in EDITABLE_ENV_KEYS]


def update_env_setting(key: str, value: str) -> None:
    if key not in EDITABLE_ENV_KEYS:
        raise ValueError(f"Unsupported env key: {key}")
    update_env_value(key, value.strip())


def _get_configured_path(key: str, env_path: Path | None = None) -> Path:
    env_values = read_env_file(env_path)
    raw_path = env_values.get(key, DEFAULT_ENV_VALUES[key])
    resolved_path = Path(raw_path)
    if resolved_path.is_absolute():
        return resolved_path
    return APP_ROOT / resolved_path


def get_configured_model_path(env_path: Path | None = None) -> Path:
    return _get_configured_path("LLAMA_MODEL_PATH", env_path)


def get_configured_source_path(env_path: Path | None = None) -> Path:
    return _get_configured_path("SOURCE_PATH", env_path)


def get_translation_block_reason() -> str | None:
    from app.utils import find_chapter_files, find_source_novels

    model_path = get_configured_model_path()
    if not model_path.is_file():
        return f"[ERROR] GGUF 모델이 없습니다. 설정을 확인해주세요."

    source_path = get_configured_source_path()
    if not source_path.exists() or not source_path.is_dir():
        return "[ERROR] 원문 폴더가 없습니다. 설정을 확인해주세요."

    novel_dirs = find_source_novels(source_path)
    has_source_files = any(find_chapter_files(novel_dir) for novel_dir in novel_dirs)
    if not has_source_files:
        return "[ERROR] 번역할 원문 txt 파일이 없습니다."

    return None


def get_runtime_settings() -> RuntimeSettings:
    load_dotenv()
    return RuntimeSettings(
        llama_server_path=_get_path("LLAMA_SERVER_PATH", DEFAULT_ENV_VALUES["LLAMA_SERVER_PATH"]),
        llama_model_path=_get_path("LLAMA_MODEL_PATH", DEFAULT_ENV_VALUES["LLAMA_MODEL_PATH"]),
        glossary_path=_get_path("GLOSSARY_PATH", DEFAULT_ENV_VALUES["GLOSSARY_PATH"]),
        source_path=_get_path("SOURCE_PATH", DEFAULT_ENV_VALUES["SOURCE_PATH"]),
        server_url=_get_str("SERVER_URL", DEFAULT_ENV_VALUES["SERVER_URL"]),
        output_root=_get_path("OUTPUT_ROOT", DEFAULT_ENV_VALUES["OUTPUT_ROOT"]),
        max_chars=_get_int("MAX_CHARS", int(DEFAULT_ENV_VALUES["MAX_CHARS"])),
        timeout=_get_int("TIMEOUT", int(DEFAULT_ENV_VALUES["TIMEOUT"])),
        draft_temperature=_get_float("DRAFT_TEMPERATURE", float(DEFAULT_ENV_VALUES["DRAFT_TEMPERATURE"])),
        refine_temperature=_get_float("REFINE_TEMPERATURE", float(DEFAULT_ENV_VALUES["REFINE_TEMPERATURE"])),
        refine_enabled=_get_bool("REFINE_ENABLED", DEFAULT_ENV_VALUES["REFINE_ENABLED"] == "on"),
        top_p=_get_float("TOP_P", float(DEFAULT_ENV_VALUES["TOP_P"])),
        n_predict=_get_int("N_PREDICT", int(DEFAULT_ENV_VALUES["N_PREDICT"])),
        ctx_size=_get_int("CTX_SIZE", int(DEFAULT_ENV_VALUES["CTX_SIZE"])),
        startup_timeout=_get_int("STARTUP_TIMEOUT", int(DEFAULT_ENV_VALUES["STARTUP_TIMEOUT"])),
    )


load_dotenv()
initialize_runtime_logging()
log_runtime_event(
    f"app init | frozen={getattr(sys, 'frozen', False)} | app_root={APP_ROOT} | "
    f"executable={getattr(sys, 'executable', '')} | argv0={sys.argv[0] if sys.argv else ''} | log_path={get_log_path()}"
)
