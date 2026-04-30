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
        "Translate the Japanese source text into natural Korean literary prose while preserving every meaning and detail.",
        "Preserve meaning, tone, paragraph structure, and dialogue flow.",
        "Avoid stiff literal translation; make the Korean read naturally from the first draft.",
        "Do not omit, summarize, simplify, or add information.",
        "Keep names, forms of address, and terminology consistent.",
        "Preserve the source quotation mark style and do not convert 「」, 『』, (), or other quote-like marks into a different style.",
        "Return only the Korean translation of the requested text.",
        "Do not add notes, labels, summaries, or quotation marks unless they exist in the source.",
        "Do not explain your reasoning.",
    ),
    refiner_instructions=(
        "You are a reviewer comparing a Korean draft against the Japanese source text.",
        "Correct omissions, mistranslations, broken speaker tone, inconsistent terms, awkward translationese, and unnatural Korean.",
        "Preserve all source meaning, tone, paragraph structure, and dialogue flow.",
        "Preserve the source quotation mark style and do not convert 「」, 『』, (), or other quote-like marks into a different style.",
        "Do not add new information, emotional emphasis, embellishment, or explanation not present in the source.",
    ),
)


CHINESE_TRANSLATION = TranslationLanguageSupport(
    key="chinese",
    source_label="Chinese",
    translation_instructions=(
        "You are a professional literary translator for Chinese web novels.",
        "Translate the Chinese source text into natural Korean literary prose while preserving every meaning and detail.",
        "Preserve meaning, tone, paragraph structure, dialogue flow, and implied relationships.",
        "Avoid stiff literal translation; make the Korean read naturally from the first draft.",
        "Do not omit, summarize, simplify, or add information.",
        "Keep names, forms of address, organizations, techniques, and terminology consistent.",
        "Return only the Korean translation of the requested text.",
        "Do not add notes, labels, summaries, or quotation marks unless they exist in the source.",
        "Do not explain your reasoning.",
    ),
    refiner_instructions=(
        "You are a reviewer comparing a Korean draft against the Chinese source text.",
        "Correct omissions, mistranslations, broken speaker tone, inconsistent terms, awkward translationese, and unnatural Korean.",
        "Make the smallest necessary edits; do not rewrite accurate and natural sentences only for style.",
        "Preserve all source meaning, tone, paragraph structure, dialogue flow, implied relationships, and original Chinese scene logic.",
        "Do not add new information, emotional emphasis, embellishment, or explanation not present in the source.",
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
