from __future__ import annotations


POSITIVE_INT_KEYS = {"MAX_CHARS", "TIMEOUT", "N_PREDICT", "CTX_SIZE", "STARTUP_TIMEOUT"}
OPTIONAL_POSITIVE_INT_KEYS = {"GPU_LAYERS", "THREADS"}
UNIT_FLOAT_KEYS = {"TOP_P"}
TARGET_LANG_ALIASES = {
    "ja": "japanese",
    "jp": "japanese",
    "japanese": "japanese",
    "zh": "chinese",
    "cn": "chinese",
    "ch": "chinese",
    "chinese": "chinese",
}


def validate_env_setting_value(key: str, new_value: str) -> str | None:
    normalized_value = new_value.strip()
    if key in OPTIONAL_POSITIVE_INT_KEYS and normalized_value.lower() == "auto":
        return None

    if "TEMPERATURE" in key or key in UNIT_FLOAT_KEYS:
        try:
            numeric_value = float(normalized_value)
        except ValueError:
            return f"[ERROR] {key} 값을 숫자로 입력해 주세요."

        if not 0.0 <= numeric_value <= 1.0:
            return f"[ERROR] {key} 값은 0.0 ~ 1.0 범위여야 합니다."

    if key in POSITIVE_INT_KEYS or key in OPTIONAL_POSITIVE_INT_KEYS:
        try:
            int_value = int(normalized_value)
        except ValueError:
            return f"[ERROR] {key} 값을 정수로 입력해 주세요."

        if int_value <= 0:
            return f"[ERROR] {key} 값은 1 이상이어야 합니다."

    if key == "REFINE_ENABLED" and normalized_value.lower() not in {"on", "off"}:
        return "[ERROR] REFINE_ENABLED는 on 또는 off만 사용할 수 있습니다."

    if key == "TARGET_LANG" and normalized_value.lower() not in TARGET_LANG_ALIASES:
        return "[WARN] TARGET_LANG는 japanese/ja/jp 또는 chinese/zh/cn/ch만 사용할 수 있습니다. 다시 입력해 주세요."

    return None


def normalize_env_setting_value(key: str, new_value: str) -> str:
    normalized_value = new_value.strip()
    if key in OPTIONAL_POSITIVE_INT_KEYS and normalized_value.lower() == "auto":
        return ""
    if key == "TARGET_LANG":
        return TARGET_LANG_ALIASES[normalized_value.lower()]
    return normalized_value


def validate_menu_number(choice: str, item_count: int) -> str | None:
    if not choice.isdigit():
        return "[ERROR] 목록 번호를 입력해 주세요."

    selected_index = int(choice) - 1
    if not 0 <= selected_index < item_count:
        return "[ERROR] 목록에 있는 번호를 입력해 주세요."

    return None


__all__ = ["normalize_env_setting_value", "validate_env_setting_value", "validate_menu_number"]
