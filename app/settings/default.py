from __future__ import annotations

import json


APP_VERSION = "1.2.3"
UPDATE_REPOSITORY = "Amunage/NovelTrans"
UPDATE_ASSET_KEYWORDS = ("NovelTrans", ".zip")
DEFAULT_MODEL_FILENAME = "gemma-4-26B-A4B-it-UD-IQ4_NL.gguf"
EDITABLE_ENV_KEYS = [
    "LLAMA_SERVER_PATH",
    "LLAMA_MODEL_PATH",
    "GLOSSARY_PATH",
    "TARGET_LANG",
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
    "GPU_LAYERS",
    "THREADS",
    "STARTUP_TIMEOUT",
]
DEFAULT_ENV_VALUES = {
    "LLAMA_SERVER_PATH": "llama/llama-server.exe",
    "LLAMA_MODEL_PATH": f"models/{DEFAULT_MODEL_FILENAME}",
    "GLOSSARY_PATH": "glossary/glossary.json",
    "TARGET_LANG": "japanese",
    "SOURCE_PATH": "source",
    "OUTPUT_ROOT": "translated",
    "SERVER_URL": "http://127.0.0.1:8080",
    "MAX_CHARS": "1400",
    "TIMEOUT": "180",
    "DRAFT_TEMPERATURE": "0.2",
    "REFINE_TEMPERATURE": "0.7",
    "REFINE_ENABLED": "on",
    "TOP_P": "0.9",
    "N_PREDICT": "2400",
    "CTX_SIZE": "8192",
    "GPU_LAYERS": "auto",
    "THREADS": "auto",
    "STARTUP_TIMEOUT": "180",
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
    },
    ensure_ascii=False,
    indent=2,
) + "\n"
