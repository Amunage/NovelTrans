from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from app.extract.site.base import Chapter, SiteExtractor


class TwkanExtractor(SiteExtractor):
    site_name = "twkan.com"
    supported_hosts = ("twkan.com", "www.twkan.com")

    def normalize_base_url(self, url: str) -> str:
        normalized_url = url.rstrip("/")
        book_match = re.search(r"/book/(\d+)(?:\.html|/index\.html)?$", normalized_url)
        if book_match:
            return f"https://twkan.com/book/{book_match.group(1)}/index.html"
        return normalized_url

    def extract_novel_title(self, soup: BeautifulSoup) -> str:
        for selector in ["h1", "title", 'meta[property="og:title"]']:
            element = soup.select_one(selector)
            if not element:
                continue
            title_source = element.get("content") if element.name == "meta" else element.get_text(strip=True)
            title = self._clean_title(title_source)
            if title:
                return self.sanitize_filename(title)

        breadcrumb_links = soup.select('a[href*="/book/"]')
        for link in breadcrumb_links:
            title = self._clean_title(link.get_text(" ", strip=True))
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
            href = link.get("href", "").strip()
            if not re.search(r"/txt/\d+/\d+", href):
                continue

            chapter_title = self._normalize_chapter_title(link.get_text(" ", strip=True))
            if not chapter_title:
                continue

            chapter_number = self._extract_chapter_number(chapter_title) or (len(chapters) + 1)
            chapters.append((chapter_number, chapter_title, urljoin(base_url, href)))

        if chapters:
            seen_urls: set[str] = set()
            unique_chapters: list[Chapter] = []
            for chapter_number, chapter_title, full_url in chapters:
                if full_url in seen_urls:
                    continue
                seen_urls.add(full_url)
                unique_chapters.append((chapter_number, chapter_title, full_url))
            unique_chapters.sort(key=lambda item: (item[0], item[2]))
            return unique_chapters

        title = self.extract_chapter_title(soup) or self.extract_novel_title(soup)
        return [(1, title, base_url)] if title else []

    def extract_chapter_title(self, soup: BeautifulSoup) -> str:
        for selector in ["h1", "title"]:
            element = soup.select_one(selector)
            if not element:
                continue
            title = self._clean_title(element.get_text(strip=True))
            if title:
                return title
        return ""

    def extract_content(self, soup: BeautifulSoup) -> str:
        for selector in [
            "#txt",
            "#content",
            ".content",
            ".book-content",
            ".read-content",
            ".yd_text2",
            "article",
            "main",
        ]:
            element = soup.select_one(selector)
            content = self._extract_content_from_element(element)
            if content:
                return content

        title_element = soup.select_one("h1")
        content = self._extract_content_after_title(title_element)
        if content:
            return content

        return self._extract_content_by_fallback(soup)

    def _clean_title(self, value: str) -> str:
        title = re.split(r"\s*[,，\-|_]\s*", value)[0].strip()
        title = re.sub(r"(?:最新章節|結局|無防盜.*|在線閱讀.*)$", "", title).strip()
        return title

    def _normalize_chapter_title(self, value: str) -> str:
        title = re.sub(r"\s+", " ", value).strip()
        title = re.sub(r"\s+\d{4}-\d{2}-\d{2}$", "", title).strip()
        return title

    def _extract_chapter_number(self, title: str) -> int | None:
        match = re.search(r"第\s*(\d+)", title)
        return int(match.group(1)) if match else None

    def _extract_content_from_element(self, element) -> str:
        if element is None:
            return ""

        working = BeautifulSoup(str(element), "html.parser")
        for removable in working.select(
            "script, style, noscript, nav, footer, header, aside, .ads, .ad, .toolbar, .tools, .paging"
        ):
            removable.decompose()

        for br in working.find_all("br"):
            br.replace_with("\n")
        for paragraph in working.find_all(["p", "div"]):
            paragraph.insert_after("\n")

        text = self.clean_text(working.get_text("\n"))
        return "" if self._looks_like_navigation_block(text) else text

    def _extract_content_after_title(self, title_element) -> str:
        if title_element is None:
            return ""

        parts: list[str] = []
        for sibling in title_element.next_siblings:
            name = getattr(sibling, "name", None)
            text = sibling.get_text("\n", strip=True) if hasattr(sibling, "get_text") else str(sibling).strip()
            if not text:
                continue
            if self._is_stop_text(text, name):
                break
            if self._looks_like_metadata_line(text):
                continue
            parts.append(text)

        return self.clean_text("\n\n".join(parts))

    def _extract_content_by_fallback(self, soup: BeautifulSoup) -> str:
        candidates: list[str] = []
        for element in soup.find_all(["article", "main", "section", "div"]):
            text = self._extract_content_from_element(element)
            if len(text) >= 400:
                candidates.append(text)

        if not candidates:
            return ""

        candidates.sort(key=len, reverse=True)
        for candidate in candidates:
            if not self._looks_like_navigation_block(candidate):
                return candidate
        return candidates[0]

    def _is_stop_text(self, text: str, tag_name: str | None) -> bool:
        if tag_name in {"nav", "footer", "aside"}:
            return True
        stop_markers = ("上一章", "下一章", "目錄", "书页", "書頁", "收藏", "设置", "設置", "關閉")
        return any(marker in text for marker in stop_markers)

    def _looks_like_metadata_line(self, text: str) -> bool:
        return bool(re.search(r"\d{4}-\d{2}-\d{2}.*作者", text))

    def _looks_like_navigation_block(self, text: str) -> bool:
        navigation_markers = ("上一章", "下一章", "目錄", "书页", "書頁", "首頁", "收藏", "關閉")
        marker_hits = sum(1 for marker in navigation_markers if marker in text)
        return marker_hits >= 3 and len(text) < 1200