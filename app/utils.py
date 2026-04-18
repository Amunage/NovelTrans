from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from app.config import SEPARATOR_LINE


@dataclass
class SourceDocument:
    title: str
    body: str
    source_path: Path


def find_source_novels(source_root: Path) -> list[Path]:
    if not source_root.exists() or not source_root.is_dir():
        return []

    return sorted([path for path in source_root.iterdir() if path.is_dir()], key=lambda path: path.name)


def find_chapter_files(novel_dir: Path) -> list[Path]:
    return sorted(
        [path for path in novel_dir.iterdir() if path.is_file() and path.suffix.lower() == ".txt"],
        key=lambda path: path.name.lower(),
    )


def parse_chapter_selection(raw: str) -> tuple[int, int] | None:
    value = raw.strip()
    if not value:
        return None

    if value.isdigit():
        chapter_number = int(value)
        return chapter_number, chapter_number

    range_match = re.fullmatch(r"(\d+)\s*~\s*(\d+)", value)
    if not range_match:
        return None

    start_number = int(range_match.group(1))
    end_number = int(range_match.group(2))
    if start_number > end_number:
        return None

    return start_number, end_number


def parse_source_file(path: Path) -> SourceDocument:
    raw_text = path.read_text(encoding="utf-8")
    lines = raw_text.splitlines()
    if not lines:
        raise ValueError(f"Source file is empty: {path}")

    has_explicit_title = len(lines) > 1 and lines[0].strip() and lines[1] == SEPARATOR_LINE

    if has_explicit_title:
        title = lines[0].strip()
        body_lines = lines[2:]
        while body_lines and not body_lines[0].strip():
            body_lines.pop(0)
        body = "\n".join(body_lines).strip()
    else:
        title = path.stem
        body = raw_text.strip()

    if not body:
        raise ValueError(f"Source body is empty: {path}")

    return SourceDocument(title=title, body=body, source_path=path)


def split_into_chunks(text: str, max_chunk_chars: int) -> list[str]:
    paragraphs = [paragraph.strip() for paragraph in re.split(r"\n{2,}", text) if paragraph.strip()]
    if not paragraphs:
        return []

    chunks: list[str] = []
    current_chunk: list[str] = []
    current_length = 0

    for paragraph in paragraphs:
        if len(paragraph) > max_chunk_chars:
            if current_chunk:
                chunks.append("\n\n".join(current_chunk))
                current_chunk = []
                current_length = 0
            chunks.extend(split_large_paragraph(paragraph, max_chunk_chars))
            continue

        addition = len(paragraph) if not current_chunk else len(paragraph) + 2
        if current_chunk and current_length + addition > max_chunk_chars:
            chunks.append("\n\n".join(current_chunk))
            current_chunk = [paragraph]
            current_length = len(paragraph)
        else:
            current_chunk.append(paragraph)
            current_length += addition

    if current_chunk:
        chunks.append("\n\n".join(current_chunk))

    return chunks


def split_large_paragraph(paragraph: str, max_chunk_chars: int) -> list[str]:
    if len(paragraph) <= max_chunk_chars:
        return [paragraph]

    lines = [line.strip() for line in paragraph.splitlines() if line.strip()]
    if len(lines) > 1:
        parts: list[str] = []
        current: list[str] = []
        current_length = 0
        for line in lines:
            addition = len(line) if not current else len(line) + 1
            if current and current_length + addition > max_chunk_chars:
                parts.append("\n".join(current))
                current = [line]
                current_length = len(line)
            else:
                current.append(line)
                current_length += addition
        if current:
            parts.append("\n".join(current))
        return parts

    return [paragraph[index : index + max_chunk_chars] for index in range(0, len(paragraph), max_chunk_chars)]


def sanitize_model_text(text: str | None) -> str | None:
    if text is None:
        return None

    sanitized = text.replace("\r\n", "\n").replace("\r", "\n")
    sanitized = re.sub(r"<\|channel\>\s*thought\s*", "", sanitized, flags=re.IGNORECASE)
    sanitized = re.sub(r"<channel\|>", "", sanitized, flags=re.IGNORECASE)
    sanitized = re.sub(r"<\|[^>\n]+\|>", "", sanitized)
    sanitized = re.sub(r"<\|[^>\n]+>[A-Za-z_-]*", "", sanitized)
    sanitized = re.sub(r"<[^<>\n]*\|>", "", sanitized)
    sanitized = re.sub(r"\n{3,}", "\n\n", sanitized)
    return sanitized.strip()


def normalize_translation(text: str) -> str:
    normalized = sanitize_model_text(text) or ""
    normalized = re.sub(r"\A(?:Korean translation:|Translation:)\s*", "", normalized, flags=re.IGNORECASE)
    marker_pattern = re.compile(
        r"</?\s*(chapter_title|previous_source|previous_translation|current_source|previous_refined|current_text|title|glossary)\s*>",
        flags=re.IGNORECASE,
    )
    marker_match = marker_pattern.search(normalized)
    if marker_match:
        normalized = normalized[:marker_match.start()].rstrip()
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()


def print_progress(
    stage: str,
    current: int,
    total: int,
    *,
    label: str | None = None,
    status: str | None = None,
    width: int = 24,
):
    safe_total = max(total, 1)
    safe_current = min(max(current, 0), safe_total)
    filled = int(width * safe_current / safe_total)
    percent = int((safe_current / safe_total) * 100)
    bar = "█" * filled + "░" * (width - filled)
    status_text = status or (f"{stage}중..." if not stage.endswith("...") else stage)
    label_text = f"{label} " if label else ""
    message = f"\r[RUN] {bar} {percent:3d}% {safe_current}/{safe_total} | {label_text}{status_text}"
    end = "\n" if safe_current >= safe_total else ""
    print(message, end=end, flush=True)
