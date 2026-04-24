from __future__ import annotations

import re
from pathlib import Path

from app.terms.base import GlossaryLanguageSupport
from app.terms.candidate import TermExtractionConfig, extract_candidates
from app.terms.wordlist import has_word


TERM_PATTERN = re.compile(r"[\u3400-\u9fff]{2,10}(?:[·・][\u3400-\u9fff]{1,8})?")
MIN_TERM_COUNT = 5
MIN_FILE_COUNT = 1
MAX_CANDIDATES = 300
MIN_TERM_SCORE = 4.0
CHINESE_DICT_FILENAME = "chinese_dict.txt"
STOP_TERMS = {
    "这个",
    "那个",
    "一个",
    "自己",
    "我们",
    "你们",
    "他们",
    "不是",
    "没有",
    "已经",
    "如果",
    "因为",
    "所以",
    "然后",
    "但是",
    "只是",
    "真的",
    "什么",
    "怎么",
    "事情",
    "地方",
    "时候",
    "东西",
    "问题",
    "大家",
    "今天",
    "昨天",
    "现在",
    "这里",
    "那里",
}
COMMON_TWO_CHAR_TERMS = {
    "今天",
    "现在",
    "然后",
    "已经",
    "因为",
    "所以",
    "如果",
    "这样",
    "那样",
    "没有",
    "不是",
    "自己",
    "什么",
    "怎么",
    "事情",
    "地方",
    "时候",
    "东西",
}
NAME_SUFFIXES = ("先生", "小姐", "老师", "同学", "学姐", "学长", "医生", "总", "哥", "姐")
ORG_SUFFIXES = (
    "学院",
    "大学",
    "高中",
    "中学",
    "公司",
    "集团",
    "公会",
    "协会",
    "骑士团",
    "冒险者公会",
    "部",
    "社",
    "队",
    "军",
    "门",
    "宗",
    "派",
    "阁",
    "殿",
)
LOCATION_SUFFIXES = ("国", "帝国", "王国", "城", "村", "镇", "街", "路", "山", "湖", "岛", "州")
GLOSSARY_SYSTEM_INSTRUCTIONS = [
    "You are reviewing Chinese glossary candidates for a Korean novel translation glossary.",
    "Keep only proper nouns, person names, place names, organizations, schools, techniques, item names, and fixed story-specific terms.",
    "Remove everyday vocabulary, pronouns, abstract nouns, generic scene words, and non-terms.",
    "Return a JSON object only.",
    "Keys must stay in Chinese exactly as provided.",
    "Values must be concise, natural Korean glossary entries.",
    "Do not include explanations or markdown.",
]


def _normalize_term(term: str) -> str:
    normalized = term.strip("·・")
    normalized = re.sub(r"\s+", "", normalized)
    return normalized.strip()


def _iter_term_matches(text: str):
    yield from TERM_PATTERN.finditer(text)


def _has_name_suffix(term: str) -> bool:
    return any(term.endswith(suffix) and len(term) > len(suffix) for suffix in NAME_SUFFIXES)


def _has_org_suffix(term: str) -> bool:
    return any(term.endswith(suffix) and len(term) > len(suffix) for suffix in ORG_SUFFIXES)


def _has_location_suffix(term: str) -> bool:
    return any(term.endswith(suffix) and len(term) > len(suffix) for suffix in LOCATION_SUFFIXES)


def _is_dictionary_word(term: str) -> bool:
    compact = term.replace("·", "").replace("・", "")
    if len(compact) < 2:
        return False
    return has_word(CHINESE_DICT_FILENAME, compact)


def _is_valid_term(term: str) -> bool:
    compact = term.replace("·", "").replace("・", "")
    if len(compact) < 2 or len(compact) > 12:
        return False
    if compact in STOP_TERMS:
        return False
    if len(compact) == 2 and compact in COMMON_TWO_CHAR_TERMS:
        return False
    if compact.startswith(("这个", "那个", "一个", "我们", "你们", "他们", "如果", "因为", "所以")):
        return False
    if compact.endswith(("的话", "的人", "起来", "下去", "不过", "时候", "东西", "问题", "事情", "地方")):
        return False
    if compact[-1] in {"的", "了", "着", "过", "吗", "呢", "啊", "呀", "吧", "嘛", "哦"}:
        return False
    if len(set(compact)) == 1:
        return False
    return True


def _has_honorific_context(term: str, sentences: list[str]) -> bool:
    markers = [f"{term}{suffix}" for suffix in NAME_SUFFIXES]
    markers.extend((f"叫{term}", f"是{term}", f"找{term}", f"和{term}"))
    return any(marker in sentence for sentence in sentences for marker in markers)


def _has_cooccurring_proper_noun(term: str, sentences: list[str]) -> bool:
    for sentence in sentences:
        found_terms: set[str] = set()
        for match in _iter_term_matches(sentence):
            candidate = _normalize_term(match.group(0))
            if candidate == term or not _is_valid_term(candidate):
                continue
            found_terms.add(candidate)
        if found_terms:
            return True
    return False


def _score_term(term: str, count: int, file_count: int, sentences: list[str]) -> float:
    score = 0.0
    score += min(count, 10) * 0.3
    score += min(file_count, 6) * 1.25
    if len(term) >= 3:
        score += 0.5
    if _has_name_suffix(term):
        score += 1.8
    if _has_org_suffix(term):
        score += 1.6
    if _has_location_suffix(term):
        score += 1.1
    if "·" in term or "・" in term:
        score += 1.4
    if _has_honorific_context(term, sentences):
        score += 1.6
    if _has_cooccurring_proper_noun(term, sentences):
        score += 1.0
    return score


def _reject_candidate(term: str, sentences: list[str]) -> bool:
    return (
        _is_dictionary_word(term)
        and not _has_name_suffix(term)
        and not _has_org_suffix(term)
        and not _has_location_suffix(term)
        and not _has_honorific_context(term, sentences)
    )


def build_refine_prompt(novel_name: str, candidates: list[tuple[str, str]]) -> str:
    prompt_lines = GLOSSARY_SYSTEM_INSTRUCTIONS.copy()
    prompt_lines.append(f"Novel: {novel_name}")
    prompt_lines.append("Review the following candidate glossary entries.")
    prompt_lines.append("Each line is formatted as: Chinese term => example sentence")
    prompt_lines.append("<candidates>")
    for term, example in candidates:
        prompt_lines.append(f"{term} => {example}")
    prompt_lines.append("</candidates>")
    prompt_lines.append(
        'Return strict JSON like {"天海学院":"텐카이 학원","林晚":"린 완"} and include only accepted glossary entries.'
    )
    return "\n".join(prompt_lines)


def extract_glossary_candidates(novel_dir: Path, min_term_count: int = MIN_TERM_COUNT) -> dict[str, str]:
    return extract_candidates(
        novel_dir,
        TermExtractionConfig(
            iter_matches=_iter_term_matches,
            normalize_term=_normalize_term,
            is_valid_term=_is_valid_term,
            reject_candidate=_reject_candidate,
            score_term=_score_term,
            min_term_count=min_term_count,
            min_file_count=MIN_FILE_COUNT,
            min_term_score=MIN_TERM_SCORE,
            max_candidates=MAX_CANDIDATES,
        ),
    )


CHINESE_GLOSSARY = GlossaryLanguageSupport(
    key="chinese",
    source_label="Chinese",
    extract_glossary_candidates=extract_glossary_candidates,
    build_refine_prompt=build_refine_prompt,
    default_min_term_count=MIN_TERM_COUNT,
)
