from __future__ import annotations

import re

from bs4 import BeautifulSoup

from app.sites.base import Chapter, SiteExtractor


class KakuyomuExtractor(SiteExtractor):
    site_name = "kakuyomu.jp"
    supported_hosts = ("kakuyomu.jp",)

    def normalize_base_url(self, url: str) -> str:
        return url.rstrip("/")

    def extract_novel_title(self, soup: BeautifulSoup) -> str:
        for selector in ["h1", "title", 'meta[property="og:title"]']:
            element = soup.select_one(selector)
            if not element:
                continue
            title_source = element.get("content") if element.name == "meta" else element.get_text(strip=True)
            title = re.split(r"\s*[-|]\s*", title_source)[0].strip()
            if title:
                return self.sanitize_filename(title)
        return "unknown_novel"

    def extract_chapter_links(
        self,
        base_url: str,
        soup: BeautifulSoup,
        fetch_page,
    ) -> list[Chapter]:
        chapters: list[tuple[int, str, str]] = []

        for link in soup.select('a[href*="/episodes/"]'):
            href = link.get("href", "")
            if not re.search(r"/works/\d+/episodes/\d+$", href):
                continue

            chapter_title = self._normalize_episode_title(link.get_text(" ", strip=True))
            if chapter_title == "1話目から読む":
                continue

            chapter_num = len(chapters) + 1
            chapters.append((chapter_num, chapter_title or f"Chapter {chapter_num}", href))

        return self.build_chapters_from_links(base_url, chapters)

    def extract_chapter_title(self, soup: BeautifulSoup) -> str:
        for selector in [".widget-episodeTitle", "h1", "title"]:
            element = soup.select_one(selector)
            if element:
                return element.get_text(strip=True)
        return ""

    def extract_content(self, soup: BeautifulSoup) -> str:
        body_element = soup.select_one(".widget-episodeBody")
        if body_element is None:
            return ""

        for br in body_element.find_all("br"):
            br.replace_with("\n")
        for paragraph in body_element.find_all("p"):
            paragraph.insert_after("\n")

        return self.clean_text(body_element.get_text())

    def _normalize_episode_title(self, raw_title: str) -> str:
        return re.sub(r"\s+\d{4}年\d{1,2}月\d{1,2}日\s+公開$", "", raw_title).strip()
