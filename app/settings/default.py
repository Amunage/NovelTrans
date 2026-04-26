from __future__ import annotations

import json


APP_VERSION = "1.2.3"
UPDATE_REPOSITORY = "Amunage/NovelTrans"
UPDATE_ASSET_KEYWORDS = ("NovelTrans", ".zip")
DEFAULT_MODEL_FILENAME = "gemma-4-E4B-it-Q4_K_M.gguf"
EDITABLE_ENV_KEYS = [
    "LLAMA_SERVER_PATH",
    "LLAMA_MODEL_PATH",
    "GLOSSARY_PATH",
    "SERVER_URL",
    "SOURCE_PATH",
    "OUTPUT_ROOT",
    "TARGET_LANG",
    "STARTUP_TIMEOUT",
    "REQUEST_TIMEOUT",
    "DRAFT_TEMPERATURE",
    "REFINE_TEMPERATURE",
    "AUTO_REFINE",
    "TOP_P",
    "MAX_CHARS",
    "MAX_TOKENS",
    "CTX_SIZE",
    "GPU_LAYERS",
    "THREADS",
    "DEBUG_MODE",
]
DEFAULT_ENV_VALUES = {
    "LLAMA_SERVER_PATH": "llama/llama-server.exe",
    "LLAMA_MODEL_PATH": f"models/{DEFAULT_MODEL_FILENAME}",
    "GLOSSARY_PATH": "glossary/default.json",
    "SERVER_URL": "http://127.0.0.1:8080",
    "SOURCE_PATH": "source",
    "OUTPUT_ROOT": "translated",
    "TARGET_LANG": "japanese",
    "STARTUP_TIMEOUT": "180",
    "REQUEST_TIMEOUT": "180",
    "DRAFT_TEMPERATURE": "0.2",
    "REFINE_TEMPERATURE": "0.7",
    "AUTO_REFINE": "true",
    "TOP_P": "0.9",
    "MAX_CHARS": "1400",
    "MAX_TOKENS": "2400",
    "CTX_SIZE": "8192",
    "GPU_LAYERS": "auto",
    "THREADS": "auto",
    "DEBUG_MODE": "false",
}
DEFAULT_ENV_CONTENT = "\n".join(f"{key}={value}" for key, value in DEFAULT_ENV_VALUES.items()) + "\n"
DEFAULT_GLOSSARY_CONTENT = """{
}
"""
DEFAULT_SEPARATOR_LINE = "=" * 60
DEFAULT_PROMPT_CONTENT = json.dumps(
    {
        "translation_instructions": "",
        "refiner_instructions": "",
        "glossary_instructions": "",
    },
    ensure_ascii=False,
    indent=2,
) + "\n"
