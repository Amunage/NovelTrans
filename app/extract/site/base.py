from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import TypeAlias
from urllib.parse import urljoin

from bs4 import BeautifulSoup


Chapter: TypeAlias = tuple[int, str, str]


class SiteExtractor(ABC):
    site_name = "unknown"
    supported_hosts: tuple[str, ...] = ()

    @classmethod
    def supports(cls, host: str) -> bool:
        normalized_host = host.lower()
        return any(
            normalized_host == supported_host or normalized_host.endswith(f".{supported_host}")
            for supported_host in cls.supported_hosts
        )

    def prepare_session(self, session) -> None:
        return None

    def normalize_base_url(self, url: str) -> str:
        return url.rstrip("/") + "/"

    @abstractmethod
    def extract_novel_title(self, soup: BeautifulSoup) -> str:
        raise NotImplementedError

    @abstractmethod
    def extract_chapter_links(
        self,
        base_url: str,
        soup: BeautifulSoup,
        fetch_page,
    ) -> list[Chapter]:
        raise NotImplementedError

    @abstractmethod
    def extract_chapter_title(self, soup: BeautifulSoup) -> str:
        raise NotImplementedError

    @abstractmethod
    def extract_content(self, soup: BeautifulSoup) -> str:
        raise NotImplementedError

    def clean_text(self, text: str) -> str:
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        lines = [line.strip() for line in text.split("\n")]
        text = "\n".join(lines)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def sanitize_filename(self, filename: str) -> str:
        filename = re.sub(r'[<>:"/\\|?*]', "", filename)
        filename = filename.strip(". ")
        if len(filename) > 100:
            filename = filename[:100]
        return filename or "unknown_novel"

    def build_chapters_from_links(
        self,
        base_url: str,
        links: list[tuple[int, str, str]],
    ) -> list[Chapter]:
        seen: set[str] = set()
        unique_chapters: list[Chapter] = []

        for chapter_num, chapter_title, href in links:
            full_url = urljoin(base_url, href)
            if full_url in seen:
                continue
            seen.add(full_url)
            unique_chapters.append((chapter_num, chapter_title, full_url))

        unique_chapters.sort(key=lambda item: item[0])
        return unique_chapters