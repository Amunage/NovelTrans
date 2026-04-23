from __future__ import annotations

import ctypes
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from app.settings.default import (
    DEFAULT_ENV_CONTENT,
    DEFAULT_ENV_VALUES,
    EDITABLE_ENV_KEYS,
)


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
    return Path(__file__).resolve().parents[2]


def get_distribution_root(app_root: Path) -> Path:
    if getattr(sys, "frozen", False) and app_root.name.lower() == "runtime":
        return app_root.parent
    return app_root


APP_ROOT = get_app_root()
DIST_ROOT = get_distribution_root(APP_ROOT)
DATA_ROOT = DIST_ROOT / "data"
DATA_USER_ROOT = DATA_ROOT / "user"
PROMPT_SETTINGS_PATH = DATA_USER_ROOT / "custom_prompt.json"
ENV_PATH = DATA_USER_ROOT / ".env"


@dataclass(frozen=True)
class RuntimeSettings:
    llama_server_path: Path
    llama_model_path: Path
    glossary_path: Path
    target_lang: str
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
    gpu_layers: int | None
    threads: int | None
    startup_timeout: int


def _warn_invalid_env(name: str, value: str, default: object) -> None:
    print(f"[WARNING] .env value is invalid, using default: {name}={value!r} -> {default!r}")


def load_dotenv() -> None:
    env_path = ENV_PATH
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        normalized_key = key.strip()
        normalized_value = value.strip().strip('"').strip("'")
        os.environ.setdefault(normalized_key, normalized_value)


def _get_str(name: str, default: str) -> str:
    return os.getenv(name, default)


def _get_path(name: str, default: str) -> Path:
    raw_value = _get_str(name, default)
    path = Path(raw_value)
    if path.is_absolute():
        return path
    return DATA_ROOT / path


def _get_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default

    try:
        return int(value)
    except ValueError:
        _warn_invalid_env(name, value, default)
        return default


def _get_optional_int(name: str) -> int | None:
    value = os.getenv(name)
    if value is None:
        return None

    normalized = value.strip()
    if not normalized or normalized.lower() == "auto":
        return None

    try:
        return int(normalized)
    except ValueError:
        _warn_invalid_env(name, value, None)
        return None


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


def _get_target_lang(default: str) -> str:
    value = os.getenv("TARGET_LANG")
    if value is None:
        return default

    normalized = value.strip().lower()
    aliases = {
        "ja": "japanese",
        "jp": "japanese",
        "japanese": "japanese",
        "zh": "chinese",
        "cn": "chinese",
        "ch": "chinese",
        "chinese": "chinese",
    }
    resolved = aliases.get(normalized)
    if resolved is None:
        _warn_invalid_env("TARGET_LANG", value, default)
        return default
    return resolved


def read_env_file(env_path: Path | None = None) -> dict[str, str]:
    resolved_env_path = env_path or ENV_PATH
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
    resolved_env_path = env_path or ENV_PATH
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
    os.environ[key] = value


def get_env_settings_items() -> list[tuple[str, str]]:
    env_values = read_env_file()
    return [(key, env_values.get(key, DEFAULT_ENV_VALUES.get(key, ""))) for key in EDITABLE_ENV_KEYS]


def update_env_setting(key: str, value: str) -> None:
    if key not in EDITABLE_ENV_KEYS:
        raise ValueError(f"Unsupported env key: {key}")
    update_env_value(key, value.strip())


def reset_env_settings_to_defaults(env_path: Path | None = None) -> Path:
    from app.settings.logging import log_runtime_event

    resolved_env_path = env_path or ENV_PATH
    resolved_env_path.write_text(DEFAULT_ENV_CONTENT, encoding="utf-8")
    for key, value in DEFAULT_ENV_VALUES.items():
        os.environ[key] = value
    log_runtime_event(f"reset env settings to defaults | path={resolved_env_path}")
    return resolved_env_path


def _get_configured_path(key: str, env_path: Path | None = None) -> Path:
    env_values = read_env_file(env_path)
    raw_path = env_values.get(key, DEFAULT_ENV_VALUES[key])
    resolved_path = Path(raw_path)
    if resolved_path.is_absolute():
        return resolved_path
    return DATA_ROOT / resolved_path


def get_configured_model_path(env_path: Path | None = None) -> Path:
    return _get_configured_path("LLAMA_MODEL_PATH", env_path)


def get_configured_source_path(env_path: Path | None = None) -> Path:
    return _get_configured_path("SOURCE_PATH", env_path)


def get_runtime_settings() -> RuntimeSettings:
    load_dotenv()
    return RuntimeSettings(
        llama_server_path=_get_path("LLAMA_SERVER_PATH", DEFAULT_ENV_VALUES["LLAMA_SERVER_PATH"]),
        llama_model_path=_get_path("LLAMA_MODEL_PATH", DEFAULT_ENV_VALUES["LLAMA_MODEL_PATH"]),
        glossary_path=_get_path("GLOSSARY_PATH", DEFAULT_ENV_VALUES["GLOSSARY_PATH"]),
        target_lang=_get_target_lang(DEFAULT_ENV_VALUES["TARGET_LANG"]),
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
        gpu_layers=_get_optional_int("GPU_LAYERS"),
        threads=_get_optional_int("THREADS"),
        startup_timeout=_get_int("STARTUP_TIMEOUT", int(DEFAULT_ENV_VALUES["STARTUP_TIMEOUT"])),
    )


load_dotenv()
