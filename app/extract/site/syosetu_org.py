from __future__ import annotations

import re

from bs4 import BeautifulSoup

from app.extract.site.base import Chapter, SiteExtractor


class SyosetuOrgExtractor(SiteExtractor):
    site_name = "syosetu.org"
    supported_hosts = ("syosetu.org",)

    def prepare_session(self, session) -> None:
        session.cookies.set("over18", "yes", domain="syosetu.org")
        session.cookies.set("over18", "yes", domain=".syosetu.org")

    def extract_novel_title(self, soup: BeautifulSoup) -> str:
        for tag, attrs in [("title", {}), ("h1", {}), ("meta", {"property": "og:title"})]:
            if tag == "meta":
                element = soup.find(tag, attrs)
                if element and element.get("content"):
                    title = re.split(r"\s*[-|]\s*", element.get("content"))[0].strip()
                    if title:
                        return self.sanitize_filename(title)
                continue

            element = soup.find(tag, **attrs)
            if element:
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
        chapters: list[tuple[int, str, str]] = []

        for link in soup.find_all("a", href=True):
            href = link.get("href", "")
            match = re.search(r"(?:\./)?(\d+)\.html$", href)
            if not match:
                continue

            chapter_num = int(match.group(1))
            chapter_title = link.get_text(strip=True) or f"Chapter {chapter_num}"
            chapters.append((chapter_num, chapter_title, href))

        return self.build_chapters_from_links(base_url, chapters)

    def extract_chapter_title(self, soup: BeautifulSoup) -> str:
        for tag, attrs in [
            ("h1", {"class_": "novel_subtitle"}),
            ("h1", {}),
            ("div", {"class_": "novel_subtitle"}),
            ("span", {"class_": "novel_subtitle"}),
        ]:
            element = soup.find(tag, **attrs)
            if element:
                return element.get_text(strip=True)
        return ""

    def extract_content(self, soup: BeautifulSoup) -> str:
        body_element = None
        for selector in [
            {"id": "honbun"},
            {"class_": "honbun"},
            {"id": "novel_honbun"},
            {"class_": "novel_view"},
            {"id": "novel_view"},
        ]:
            body_element = soup.find("div", **selector)
            if body_element:
                break

        if body_element is None:
            all_divs = soup.find_all("div")
            if not all_divs:
                return ""
            body_element = max(all_divs, key=lambda div: len(div.get_text()))

        for br in body_element.find_all("br"):
            br.replace_with("\n")
        for paragraph in body_element.find_all("p"):
            paragraph.insert_after("\n")

        return self.clean_text(body_element.get_text())