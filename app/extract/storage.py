from __future__ import annotations

from pathlib import Path

from app.settings.prompt import SEPARATOR_LINE


def format_chapter_document(title: str, content: str) -> str:
    return f"{title}\n{SEPARATOR_LINE}\n\n{content}"


def get_novel_output_path(novel_title: str | None, output_dir: Path) -> Path:
    novel_folder = novel_title or "unknown_novel"
    full_path = output_dir / novel_folder
    full_path.mkdir(parents=True, exist_ok=True)
    return full_path


def save_chapter_file(num: int, title: str, content: str, output_path: Path) -> None:
    filepath = output_path / f"{num:04d}.txt"
    filepath.write_text(format_chapter_document(title, content), encoding="utf-8")


__all__ = ["format_chapter_document", "get_novel_output_path", "save_chapter_file"]