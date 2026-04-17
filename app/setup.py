from __future__ import annotations

import sys
from pathlib import Path


def get_app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


DEFAULT_ENV_CONTENT = """LLAMA_SERVER_PATH=D:\\llama.cpp\\llama-server.exe
LLAMA_MODEL_PATH=D:\\llama.cpp\\models\\model.gguf
GLOSSARY_PATH=glossary/glossary.json
SOURCE_PATH=source
OUTPUT_ROOT=translated
SERVER_URL=http://127.0.0.1:8080
MAX_CHARS=1400
TIMEOUT=180
DRAFT_TEMPERATURE=0.2
REFINE_TEMPERATURE=0.45
TOP_P=0.9
N_PREDICT=1800
CTX_SIZE=8192
STARTUP_TIMEOUT=180
"""

DEFAULT_GLOSSARY_CONTENT = """{
  "ウマ娘": "우마무스메"
}
"""


def ensure_runtime_setup() -> None:
    app_root = get_app_root()

    env_path = app_root / ".env"
    if not env_path.exists():
        env_path.write_text(DEFAULT_ENV_CONTENT, encoding="utf-8")

    glossary_dir = app_root / "glossary"
    glossary_dir.mkdir(parents=True, exist_ok=True)

    glossary_path = glossary_dir / "glossary.json"
    if not glossary_path.exists():
        glossary_path.write_text(DEFAULT_GLOSSARY_CONTENT, encoding="utf-8")
