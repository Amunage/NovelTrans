from __future__ import annotations

from app.settings.config import get_configured_model_path, get_configured_source_path, get_runtime_settings
from app.translation.engine import validate_glossary_file
from app.utils import find_chapter_files, find_source_novels


def get_translation_block_reason() -> str | None:
    model_path = get_configured_model_path()
    if not model_path.is_file():
        return "[ERROR] GGUF 모델이 없습니다. 설정을 확인해주세요."

    glossary_warning = validate_glossary_file(get_runtime_settings().glossary_path)
    if glossary_warning is not None:
        return glossary_warning

    source_path = get_configured_source_path()
    if not source_path.exists() or not source_path.is_dir():
        return "[ERROR] 원문 폴더가 없습니다. 설정을 확인해주세요."

    novel_dirs = find_source_novels(source_path)
    has_source_files = any(find_chapter_files(novel_dir) for novel_dir in novel_dirs)
    if not has_source_files:
        return "[ERROR] 번역할 원문 txt 파일이 없습니다."

    return None


def get_glossary_candidate_block_reason() -> str | None:
    model_path = get_configured_model_path()
    if not model_path.is_file():
        return "[ERROR] GGUF 모델이 없습니다. 설정을 확인해 주세요."

    source_path = get_configured_source_path()
    if not source_path.exists() or not source_path.is_dir():
        return "[ERROR] 원문 폴더가 없습니다. 설정을 확인해주세요."

    novel_dirs = find_source_novels(source_path)
    has_source_files = any(find_chapter_files(novel_dir) for novel_dir in novel_dirs)
    if not has_source_files:
        return "[ERROR] 용어 후보를 탐색할 원문 txt 파일이 없습니다."

    return None


__all__ = ["get_glossary_candidate_block_reason", "get_translation_block_reason"]
