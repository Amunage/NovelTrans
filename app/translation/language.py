from __future__ import annotations

from dataclasses import dataclass

from app.settings.config import get_runtime_settings


@dataclass(frozen=True)
class TranslationLanguageSupport:
    key: str
    source_label: str
    translation_instructions: tuple[str, ...]
    refiner_instructions: tuple[str, ...]

    def preprocess_source_text(self, text: str, *, is_title: bool) -> str:
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        if self.key == "chinese":
            normalized = normalized.replace("　", " ")
            normalized = normalized.replace("“", '"').replace("”", '"')
            normalized = normalized.replace("‘", "'").replace("’", "'")
        return normalized.strip() if is_title else normalized

    def preprocess_refine_text(self, text: str, *, is_title: bool) -> str:
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        if self.key == "chinese":
            normalized = normalized.replace("　", " ")
        return normalized.strip() if is_title else normalized


JAPANESE_TRANSLATION = TranslationLanguageSupport(
    key="japanese",
    source_label="Japanese",
    translation_instructions=(
        "You are a professional literary translator for Japanese web novels.",
        "Translate the Japanese source text into faithful Korean that still reads naturally.",
        "Preserve meaning, tone, paragraph structure, and dialogue flow.",
        "Do not omit, summarize, simplify, or add information.",
        "Keep names, forms of address, and terminology consistent.",
        "Use Korean quotation marks consistently: render spoken dialogue with double quotes (" "), inner thoughts with single quotes (' '), and handle 『』 by context as titles, nested quotes, or emphasis.",
        "Return only the Korean translation of the requested text.",
        "Do not add notes, labels, summaries, or quotation marks unless they exist in the source.",
        "Do not explain your reasoning.",
    ),
    refiner_instructions=(
        "Rewrite this Korean draft into natural Korean literary prose in a restrained, understated style.",
        "Use Korean quotation marks consistently: render spoken dialogue with double quotes (" "), inner thoughts with single quotes (' '), and handle 『』 by context as titles, nested quotes, or emphasis.",
        "Do not intensify, embellish, or over-explain.",
    ),
)


CHINESE_TRANSLATION = TranslationLanguageSupport(
    key="chinese",
    source_label="Chinese",
    translation_instructions=(
        "You are a professional literary translator for Chinese web novels.",
        "Translate the Chinese source text into faithful Korean that still reads naturally.",
        "Preserve meaning, tone, paragraph structure, dialogue flow, and implied relationships.",
        "Do not omit, summarize, simplify, or add information.",
        "Keep names, forms of address, organizations, techniques, and terminology consistent.",
        "Return only the Korean translation of the requested text.",
        "Do not add notes, labels, summaries, or quotation marks unless they exist in the source.",
        "Do not explain your reasoning.",
    ),
    refiner_instructions=(
        "Rewrite this Korean draft into natural Korean literary prose in a restrained, understated style.",
        "Preserve the original Chinese scene logic and avoid adding emotional emphasis that is not present in the source.",
        "Do not intensify, embellish, or over-explain.",
    ),
)


SUPPORTED_TRANSLATION_LANGUAGES: dict[str, TranslationLanguageSupport] = {
    JAPANESE_TRANSLATION.key: JAPANESE_TRANSLATION,
    CHINESE_TRANSLATION.key: CHINESE_TRANSLATION,
}


def get_translation_language() -> TranslationLanguageSupport:
    runtime_settings = get_runtime_settings()
    return SUPPORTED_TRANSLATION_LANGUAGES[runtime_settings.target_lang]


def get_translation_instructions() -> list[str]:
    language = get_translation_language()
    return [*language.translation_instructions]


def get_refiner_instructions() -> list[str]:
    language = get_translation_language()
    return [*language.refiner_instructions]
