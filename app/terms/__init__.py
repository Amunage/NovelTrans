from __future__ import annotations

from typing import TYPE_CHECKING

from app.settings.config import get_runtime_settings
from app.terms.chinese import CHINESE_GLOSSARY
from app.terms.japanese import JAPANESE_GLOSSARY

if TYPE_CHECKING:
    from app.terms.base import GlossaryLanguageSupport


SUPPORTED_GLOSSARY_LANGUAGES: dict[str, GlossaryLanguageSupport] = {
    JAPANESE_GLOSSARY.key: JAPANESE_GLOSSARY,
    CHINESE_GLOSSARY.key: CHINESE_GLOSSARY,
}


def get_glossary_language() -> GlossaryLanguageSupport:
    runtime_settings = get_runtime_settings()
    return SUPPORTED_GLOSSARY_LANGUAGES[runtime_settings.target_lang]


__all__ = ["get_glossary_language", "SUPPORTED_GLOSSARY_LANGUAGES"]