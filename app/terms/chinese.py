from __future__ import annotations

import re
from pathlib import Path

from app.terms.base import GLOSSARY_EXAMPLE_SENTENCE_COUNT, GlossaryLanguageSupport
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
NAME_SUFFIXES = ("先生", "小姐", "老师", "同学", "学姐", "学长", "医生", "哥哥", "姐姐", "总", "哥", "姐")
GENERIC_HONORIFIC_TERMS = frozenset(NAME_SUFFIXES) | {
    "大哥",
    "大姐",
    "哥哥",
    "姐姐",
    "老师",
    "先生",
    "小姐",
    "医生",
    "将军",
}
STRONG_ORG_SUFFIXES = (
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
    "俱乐部",
)
WEAK_ORG_SUFFIXES = (
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
ORG_SUFFIXES = STRONG_ORG_SUFFIXES + WEAK_ORG_SUFFIXES
LOCATION_SUFFIXES = ("国", "帝国", "王国", "城", "村", "镇", "街", "路", "山", "湖", "岛", "州")
FUNCTION_PREFIXES = (
    "这",
    "那",
    "我",
    "你",
    "他",
    "她",
    "它",
    "但",
    "而",
    "就",
    "都",
    "还",
    "没",
    "不",
    "把",
    "被",
    "在",
    "向",
    "对",
    "给",
    "让",
    "使",
    "的",
    "了",
    "很",
    "可",
    "只",
)
PHRASE_SUFFIXES = (
    "什么",
    "怎么",
    "时候",
    "情况",
    "起来",
    "下去",
    "一下",
    "一点",
    "一样",
    "来说",
    "可以",
    "知道",
    "觉得",
    "没有",
    "出来",
    "现在",
    "明显",
    "简单",
    "可爱",
    "遗憾",
    "理论上",
)
GENERIC_VERB_FRAGMENTS = (
    "看向",
    "点头",
    "摇头",
    "伸手",
    "开口",
    "说道",
    "问道",
    "笑了",
    "眨眼",
    "眨了",
    "叉腰",
    "挠头",
    "谢谢",
)
DISCOURSE_ENDINGS = ("说", "是")
TIME_PHRASE_PATTERN = re.compile(r"^[上下这那每]一(?:秒|刻|天|次|瞬|瞬间|时间)$")
ACTION_BODY_MARKERS = (
    "眼",
    "眼睛",
    "肩",
    "头",
    "手",
    "双手",
    "单手",
    "胸",
    "腰",
    "腿",
    "脸",
    "嘴",
    "腮",
    "下巴",
    "身",
    "身体",
    "胳膊",
    "手臂",
    "眸",
    "唇",
)
ACTION_TAIL_MARKERS = (
    "耸",
    "瞪",
    "抱",
    "叉",
    "歪",
    "摇",
    "点",
    "眨",
    "鼓",
    "抿",
    "撇",
    "皱",
    "抬",
    "低",
    "对视",
    "看",
    "盯",
    "望",
    "瞥",
    "退",
    "站",
    "坐",
    "走",
    "笑",
    "哭",
    "叫",
    "喊",
)
TERM_ENDING_PARTICLES = {"的", "了", "着", "过", "吗", "呢", "啊", "呀", "吧", "嘛", "哦"}
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


def _has_strong_org_suffix(term: str) -> bool:
    return any(term.endswith(suffix) and len(term) > len(suffix) for suffix in STRONG_ORG_SUFFIXES)


def _has_weak_org_suffix(term: str) -> bool:
    return any(term.endswith(suffix) and len(term) > len(suffix) for suffix in WEAK_ORG_SUFFIXES)


def _has_location_suffix(term: str) -> bool:
    return any(term.endswith(suffix) and len(term) > len(suffix) for suffix in LOCATION_SUFFIXES)


def _is_dictionary_word(term: str) -> bool:
    compact = term.replace("·", "").replace("・", "")
    if len(compact) < 2:
        return False
    return has_word(CHINESE_DICT_FILENAME, compact)


def _has_separator(term: str) -> bool:
    return "·" in term or "・" in term


def _has_function_edge(compact: str) -> bool:
    return compact.startswith(FUNCTION_PREFIXES) or compact.endswith(PHRASE_SUFFIXES)


def _is_discourse_or_time_phrase(compact: str) -> bool:
    return TIME_PHRASE_PATTERN.fullmatch(compact) is not None or compact.endswith(DISCOURSE_ENDINGS)


def _has_sentence_particle_inside(compact: str) -> bool:
    if len(compact) <= 2:
        return False
    return any(char in compact[1:-1] for char in "了着过")


def _has_generic_verb_fragment(compact: str) -> bool:
    return any(fragment in compact for fragment in GENERIC_VERB_FRAGMENTS)


def _looks_like_action_tail(tail: str) -> bool:
    if len(tail) < 2 or len(tail) > 6:
        return False
    has_body_marker = any(marker in tail for marker in ACTION_BODY_MARKERS)
    has_action_marker = any(marker in tail for marker in ACTION_TAIL_MARKERS)
    return has_body_marker and has_action_marker


def _has_name_like_action_tail(term: str, sentences: list[str] | None = None) -> bool:
    compact = term.replace("·", "").replace("・", "")
    if len(compact) < 4 or _has_strong_proper_noun_signal(term, sentences):
        return False

    max_prefix_length = min(4, len(compact) - 2)
    for prefix_length in range(2, max_prefix_length + 1):
        prefix = compact[:prefix_length]
        tail = compact[prefix_length:]
        if prefix in STOP_TERMS or prefix[-1] in TERM_ENDING_PARTICLES:
            continue
        if _looks_like_action_tail(tail):
            return True

    return False


def _has_strong_proper_noun_signal(term: str, sentences: list[str] | None = None) -> bool:
    return (
        _has_separator(term)
        or _has_name_suffix(term)
        or _has_strong_org_suffix(term)
        or _has_location_suffix(term)
        or (sentences is not None and _has_honorific_context(term, sentences))
    )


def _is_generic_honorific(term: str) -> bool:
    return term in GENERIC_HONORIFIC_TERMS


def _is_valid_term(term: str) -> bool:
    compact = term.replace("·", "").replace("・", "")
    if len(compact) < 2 or len(compact) > 12:
        return False
    if _is_generic_honorific(compact):
        return False
    if compact in STOP_TERMS:
        return False
    if len(compact) == 2 and compact in COMMON_TWO_CHAR_TERMS:
        return False
    if compact.startswith(("这个", "那个", "一个", "我们", "你们", "他们", "如果", "因为", "所以")):
        return False
    if _has_function_edge(compact):
        return False
    if _is_discourse_or_time_phrase(compact):
        return False
    if _has_name_like_action_tail(term):
        return False
    if compact.endswith(("的话", "的人", "不过", "东西", "问题", "事情", "地方")):
        return False
    if compact[-1] in TERM_ENDING_PARTICLES:
        return False
    if _has_sentence_particle_inside(compact) and not _has_strong_proper_noun_signal(term):
        return False
    if _has_generic_verb_fragment(compact) and not _has_strong_proper_noun_signal(term):
        return False
    if len(set(compact)) == 1:
        return False
    return True


def _has_honorific_context(term: str, sentences: list[str]) -> bool:
    markers = [f"{term}{suffix}" for suffix in NAME_SUFFIXES]
    markers.append(f"叫{term}")
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
    compact = term.replace("·", "").replace("・", "")
    score = 0.0
    score += min(count, 10) * 0.3
    score += min(file_count, 6) * 1.25
    if len(term) >= 3:
        score += 0.5
    if _has_name_suffix(term):
        score += 1.8
    if _has_strong_org_suffix(term):
        score += 1.6
    elif _has_weak_org_suffix(term) and not _is_dictionary_word(term):
        score += 0.5
    if _has_location_suffix(term):
        score += 1.1
    if _has_separator(term):
        score += 1.4
    if _has_honorific_context(term, sentences):
        score += 1.6
    if _has_cooccurring_proper_noun(term, sentences):
        score += 1.0
    if len(compact) == 2 and not _has_strong_proper_noun_signal(term, sentences):
        score -= 1.3
    if _is_dictionary_word(term):
        score -= 0.7
    return score


def _reject_candidate(term: str, sentences: list[str]) -> bool:
    compact = term.replace("·", "").replace("・", "")
    if _is_generic_honorific(compact):
        return True
    if _has_name_like_action_tail(term, sentences):
        return True
    if _has_sentence_particle_inside(compact) and not _has_strong_proper_noun_signal(term, sentences):
        return True
    if _has_generic_verb_fragment(compact) and not _has_strong_proper_noun_signal(term, sentences):
        return True
    if _is_discourse_or_time_phrase(compact) and not _has_strong_proper_noun_signal(term, sentences):
        return True
    if _is_dictionary_word(term) and len(compact) == 2:
        return True
    if _is_dictionary_word(term) and _has_weak_org_suffix(term) and not _has_strong_org_suffix(term):
        return True
    return _is_dictionary_word(term) and not _has_strong_proper_noun_signal(term, sentences)


def _strip_name_suffix(term: str) -> str:
    normalized = _normalize_term(term)
    for suffix in sorted(NAME_SUFFIXES, key=len, reverse=True):
        if normalized.endswith(suffix):
            stem = _normalize_term(normalized[: -len(suffix)])
            if len(stem) >= 2 and not _is_generic_honorific(stem):
                return stem
    return normalized


def _merge_name_suffix_variants(candidates: dict[str, list[str]]) -> dict[str, list[str]]:
    merged: dict[str, list[str]] = {}
    for term, examples in candidates.items():
        canonical_term = _strip_name_suffix(term)
        merged_examples = merged.setdefault(canonical_term, [])
        for example in examples:
            if example not in merged_examples:
                merged_examples.append(example)
            if len(merged_examples) >= GLOSSARY_EXAMPLE_SENTENCE_COUNT:
                break
    return merged


def build_refine_prompt(novel_name: str, candidates: list[tuple[str, list[str]]]) -> str:
    prompt_lines = GLOSSARY_SYSTEM_INSTRUCTIONS.copy()
    prompt_lines.append(f"Novel: {novel_name}")
    prompt_lines.append("Review the following candidate glossary entries.")
    prompt_lines.append("Each entry is formatted as: Chinese term => example sentences")
    prompt_lines.append("<candidates>")
    for term, examples in candidates:
        prompt_lines.append(f"{term} =>")
        for index, example in enumerate(examples, start=1):
            prompt_lines.append(f"  {index}. {example}")
    prompt_lines.append("</candidates>")
    prompt_lines.append(
        'Return strict JSON like {"天海学院":"텐카이 학원","林晚":"린 완"} and include only accepted glossary entries.'
    )
    return "\n".join(prompt_lines)


def extract_glossary_candidates(novel_dir: Path, min_term_count: int = MIN_TERM_COUNT) -> dict[str, list[str]]:
    candidates = extract_candidates(
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
    return _merge_name_suffix_variants(candidates)


CHINESE_GLOSSARY = GlossaryLanguageSupport(
    key="chinese",
    source_label="Chinese",
    extract_glossary_candidates=extract_glossary_candidates,
    build_refine_prompt=build_refine_prompt,
    default_min_term_count=MIN_TERM_COUNT,
)
