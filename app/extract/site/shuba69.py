from __future__ import annotations

import re

from bs4 import BeautifulSoup

from app.extract.site.base import Chapter, SiteExtractor


class Shuba69Extractor(SiteExtractor):
    site_name = "69shuba.com"
    supported_hosts = ("69shuba.com", "www.69shuba.com")

    def normalize_base_url(self, url: str) -> str:
        normalized_url = url.rstrip("/")
        book_match = re.search(r"/book/(\d+)(?:\.htm)?$", normalized_url)
        if book_match:
            return f"https://www.69shuba.com/book/{book_match.group(1)}/"
        return normalized_url + "/"

    def extract_novel_title(self, soup: BeautifulSoup) -> str:
        for selector in ["h1", "title", 'meta[property="og:title"]']:
            element = soup.select_one(selector)
            if not element:
                continue
            title_source = element.get("content") if element.name == "meta" else element.get_text(" ", strip=True)
            title = self._clean_title(title_source)
            if title:
                return self.sanitize_filename(title)
        return "unknown_novel"

    def extract_chapter_links(
        self,
        base_url: str,
        soup: BeautifulSoup,
        fetch_page,
    ) -> list[Chapter]:
        seen_urls: set[str] = set()
        chapters: list[Chapter] = []

        for link in soup.find_all("a", href=True):
            href = link.get("href", "").strip()
            if not re.search(r"/txt/\d+/\d+", href):
                continue

            full_url = self._absolute_url(base_url, href)
            if full_url in seen_urls:
                continue

            chapter_title = self._normalize_chapter_title(link.get_text(" ", strip=True))
            if not chapter_title:
                continue

            seen_urls.add(full_url)
            chapters.append((len(chapters) + 1, chapter_title, full_url))

        if chapters:
            return chapters

        title = self.extract_chapter_title(soup) or self.extract_novel_title(soup)
        return [(1, title, base_url)] if title else []

    def extract_chapter_title(self, soup: BeautifulSoup) -> str:
        for selector in ["h1", "title"]:
            element = soup.select_one(selector)
            if not element:
                continue
            title = self._clean_title(element.get_text(" ", strip=True))
            if title:
                return title
        return ""

    def extract_content(self, soup: BeautifulSoup) -> str:
        for selector in [
            "#txt",
            "#content",
            ".content",
            ".txtnav",
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

    def _absolute_url(self, base_url: str, href: str) -> str:
        if href.startswith("http://") or href.startswith("https://"):
            return href
        return f"https://www.69shuba.com{href if href.startswith('/') else '/' + href}"

    def _clean_title(self, value: str) -> str:
        title = re.sub(r"\s+", " ", value).strip()
        title = re.sub(r"\s*[-|_].*$", "", title).strip()
        title = re.sub(r"(?:最新章节|全文阅读|69书吧.*)$", "", title).strip()
        return title

    def _normalize_chapter_title(self, value: str) -> str:
        title = re.sub(r"\s+", " ", value).strip()
        title = re.sub(r"\s+\d{4}-\d{2}-\d{2}$", "", title).strip()
        return title

    def _extract_content_from_element(self, element) -> str:
        if element is None:
            return ""

        working = BeautifulSoup(str(element), "html.parser")
        for removable in working.select(
            "script, style, noscript, nav, footer, header, aside, form, .ads, .ad, .toolbar, .tools, .paging"
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
            text = sibling.get_text("\n", strip=True) if hasattr(sibling, "get_text") else str(sibling).strip()
            if not text:
                continue
            if self._is_stop_text(text):
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
            body_text = self.clean_text(soup.get_text("\n"))
            return "" if self._looks_like_navigation_block(body_text) else body_text

        candidates.sort(key=len, reverse=True)
        return candidates[0]

    def _is_stop_text(self, text: str) -> bool:
        stop_markers = ("上一章", "下一章", "目录", "目录", "书页", "阅读设置", "加入书架", "排行榜")
        return any(marker in text for marker in stop_markers)

    def _looks_like_metadata_line(self, text: str) -> bool:
        metadata_markers = ("作者：", "分类：", "连载", "完结", "更新时间", "加入书架")
        return any(marker in text for marker in metadata_markers)

    def _looks_like_navigation_block(self, text: str) -> bool:
        navigation_markers = ("上一章", "下一章", "目录", "阅读设置", "排行榜", "最近更新", "全部小说")
        marker_hits = sum(1 for marker in navigation_markers if marker in text)
        return marker_hits >= 3 and len(text) < 1600