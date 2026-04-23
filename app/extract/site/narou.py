from __future__ import annotations

import re

from bs4 import BeautifulSoup

from app.extract.site.base import Chapter, SiteExtractor


class NarouExtractor(SiteExtractor):
    site_name = "syosetu.com"
    supported_hosts = (
        "ncode.syosetu.com",
        "novel18.syosetu.com",
        "novelcom.syosetu.com",
        "noc.syosetu.com",
        "mnlt.syosetu.com",
    )

    def prepare_session(self, session) -> None:
        session.cookies.set("over18", "yes", domain="syosetu.com")
        session.cookies.set("over18", "yes", domain=".syosetu.com")

    def normalize_base_url(self, url: str) -> str:
        return url.rstrip("/") + "/"

    def extract_novel_title(self, soup: BeautifulSoup) -> str:
        for selector in [".p-novel__title", "h1", "title"]:
            element = soup.select_one(selector)
            if not element:
                continue
            title = re.split(r"\s*[-|]\s*", element.get_text(strip=True))[0].strip()
            if title:
                return self.sanitize_filename(title)
        return "unknown_novel"

    def extract_chapter_links(
        self,
        base_url: str,
        soup: BeautifulSoup,
        fetch_page,
    ) -> list[Chapter]:
        first_page_links = soup.select(".p-eplist__sublist a")
        if not first_page_links:
            title = self.extract_chapter_title(soup) or self.extract_novel_title(soup)
            return [(1, title, base_url)]

        page_numbers = self._extract_toc_page_numbers(soup)
        page_soups = [soup]
        for page_number in page_numbers:
            if page_number == 1:
                continue
            page_url = f"{base_url}?p={page_number}"
            page_soup = fetch_page(page_url)
            if page_soup is not None:
                page_soups.append(page_soup)

        chapters: list[tuple[int, str, str]] = []
        for page_soup in page_soups:
            for link in page_soup.select(".p-eplist__sublist a"):
                chapter_title = link.get_text(" ", strip=True)
                href = link.get("href", "")
                if not href:
                    continue
                chapter_num = len(chapters) + 1
                chapters.append((chapter_num, chapter_title or f"Chapter {chapter_num}", href))

        return self.build_chapters_from_links(base_url, chapters)

    def extract_chapter_title(self, soup: BeautifulSoup) -> str:
        for selector in [".p-novel__title", "h1", "title"]:
            element = soup.select_one(selector)
            if element:
                return element.get_text(strip=True)
        return ""

    def extract_content(self, soup: BeautifulSoup) -> str:
        body_element = soup.select_one(".p-novel__body") or soup.select_one(".js-novel-text")
        if body_element is None:
            return ""

        for br in body_element.find_all("br"):
            br.replace_with("\n")
        for paragraph in body_element.find_all("p"):
            paragraph.insert_after("\n")

        return self.clean_text(body_element.get_text())

    def _extract_toc_page_numbers(self, soup: BeautifulSoup) -> list[int]:
        page_numbers: set[int] = {1}
        for link in soup.select('a[href*="?p="]'):
            href = link.get("href", "")
            match = re.search(r"[?&]p=(\d+)", href)
            if match:
                page_numbers.add(int(match.group(1)))
        return sorted(page_numbers)