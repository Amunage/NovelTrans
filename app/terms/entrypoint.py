from __future__ import annotations

from app.terms import get_glossary_language
from app.terms.base import run_glossary_workflow


def main() -> int:
    return run_glossary_workflow(get_glossary_language())


__all__ = ["main"]