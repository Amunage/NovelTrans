from __future__ import annotations

import json
import re
from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup

from app.extract.site.base import Chapter, SiteExtractor


class PixivExtractor(SiteExtractor):
    site_name = "pixiv.net"
    supported_hosts = ("pixiv.net", "www.pixiv.net")

    def __init__(self) -> None:
        self.session = None
        self._novel_cache: dict[str, dict] = {}
        self._series_cache: dict[str, dict] = {}
        self._series_titles_cache: dict[str, list[dict]] = {}

    def prepare_session(self, session) -> None:
        pixiv_session = requests.Session()
        pixiv_session.headers.update(
            {
                "Referer": "https://www.pixiv.net/",
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/json, text/html, */*",
                "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
            }
        )
        self.session = pixiv_session
        return pixiv_session

    def normalize_base_url(self, url: str) -> str:
        return url.rstrip("/")

    def extract_novel_title(self, soup: BeautifulSoup) -> str:
        series_id = self._extract_series_id_from_soup(soup)
        if series_id:
            series_data = self._get_series_data(series_id)
            series_title = self._extract_title_value(series_data, "title")
            if series_title:
                return self.sanitize_filename(series_title)

        novel_id = self._extract_novel_id_from_soup(soup)
        if novel_id:
            novel_data = self._get_novel_data(novel_id)
            series_nav = novel_data.get("seriesNavData") or {}
            series_title = self._extract_title_value(series_nav, "title")
            if series_title:
                return self.sanitize_filename(series_title)

            novel_title = self._extract_title_value(novel_data, "title")
            if novel_title:
                return self.sanitize_filename(novel_title)

        for selector in ['meta[property="twitter:title"]', 'meta[property="og:title"]', "title", "h1"]:
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
        series_id = self._extract_series_id(base_url) or self._extract_series_id_from_soup(soup)
        if series_id:
            return self._build_series_chapters(series_id)

        novel_id = self._extract_novel_id(base_url) or self._extract_novel_id_from_soup(soup)
        if not novel_id:
            return []

        novel_data = self._get_novel_data(novel_id)
        series_nav = novel_data.get("seriesNavData") or {}
        linked_series_id = series_nav.get("seriesId")
        if linked_series_id:
            return self._build_series_chapters(str(linked_series_id))

        title = self._extract_title_value(novel_data, "title") or self.extract_chapter_title(soup) or "Chapter 1"
        return [(1, title, self._build_novel_url(novel_id))]

    def extract_chapter_title(self, soup: BeautifulSoup) -> str:
        novel_id = self._extract_novel_id_from_soup(soup)
        if novel_id:
            title = self._extract_title_value(self._get_novel_data(novel_id), "title")
            if title:
                return title

        for selector in ['meta[property="twitter:title"]', "h1", "title"]:
            element = soup.select_one(selector)
            if not element:
                continue
            return element.get("content") if element.name == "meta" else element.get_text(strip=True)
        return ""

    def extract_content(self, soup: BeautifulSoup) -> str:
        novel_id = self._extract_novel_id_from_soup(soup)
        if not novel_id:
            return ""

        novel_data = self._get_novel_data(novel_id)
        content = novel_data.get("content", "")
        return self.clean_text(content)

    def clean_text(self, text: str) -> str:
        text = text.replace("[newpage]", "\n\n")
        text = re.sub(r"\[chapter:([^\]]+)\]", r"\n\n\1\n\n", text)
        text = re.sub(r"\[\[rb:\s*(.*?)\s*>\s*(.*?)\s*\]\]", r"\1(\2)", text)
        text = re.sub(r"\[jump:[^\]]+\]", "", text)
        text = re.sub(r"\[pixivimage:[^\]]+\]", "", text)
        text = re.sub(r"\[uploadedimage:[^\]]+\]", "", text)
        text = re.sub(r"\[jumpuri:([^\]>]+)>\s*([^\]]+)\]", r"\1 (\2)", text)
        return super().clean_text(text)

    def _build_series_chapters(self, series_id: str) -> list[Chapter]:
        chapters: list[Chapter] = []

        for index, item in enumerate(self._get_series_content_titles(series_id), start=1):
            if not item.get("available", True):
                continue

            novel_id = str(item.get("id", "")).strip()
            if not novel_id:
                continue

            title = str(item.get("title", "")).strip() or f"Chapter {index}"
            chapters.append((index, title, self._build_novel_url(novel_id)))

        return chapters

    def _get_novel_data(self, novel_id: str) -> dict:
        if novel_id not in self._novel_cache:
            self._novel_cache[novel_id] = self._request_json(f"/ajax/novel/{novel_id}")
        return self._novel_cache[novel_id]

    def _get_series_data(self, series_id: str) -> dict:
        if series_id not in self._series_cache:
            self._series_cache[series_id] = self._request_json(f"/ajax/novel/series/{series_id}")
        return self._series_cache[series_id]

    def _get_series_content_titles(self, series_id: str) -> list[dict]:
        if series_id not in self._series_titles_cache:
            data = self._request_json(f"/ajax/novel/series/{series_id}/content_titles")
            self._series_titles_cache[series_id] = data if isinstance(data, list) else []
        return self._series_titles_cache[series_id]

    def _request_json(self, path: str) -> dict | list:
        if self.session is None:
            raise RuntimeError("Pixiv session is not initialized.")

        response = self.session.get(f"https://www.pixiv.net{path}", timeout=30)
        response.raise_for_status()
        payload = response.json()
        if payload.get("error"):
            raise RuntimeError(payload.get("message") or f"Pixiv API request failed: {path}")
        return payload.get("body") or {}

    def _extract_novel_id(self, url: str) -> str | None:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        novel_ids = query.get("id")
        if parsed.path.endswith("/show.php") and novel_ids:
            return novel_ids[0]
        return None

    def _extract_series_id(self, url: str) -> str | None:
        match = re.search(r"/novel/series/(\d+)", url)
        return match.group(1) if match else None

    def _extract_novel_id_from_soup(self, soup: BeautifulSoup) -> str | None:
        canonical = soup.select_one('link[rel="canonical"]')
        if canonical and canonical.get("href"):
            return self._extract_novel_id(canonical["href"])

        next_data = self._parse_next_data(soup)
        query = next_data.get("query", {})
        novel_id = query.get("id")
        if self._is_novel_page(next_data) and novel_id:
            return str(novel_id)
        return None

    def _extract_series_id_from_soup(self, soup: BeautifulSoup) -> str | None:
        canonical = soup.select_one('link[rel="canonical"]')
        if canonical and canonical.get("href"):
            return self._extract_series_id(canonical["href"])

        next_data = self._parse_next_data(soup)
        query = next_data.get("query", {})
        series_id = query.get("id")
        if self._is_series_page(next_data) and series_id:
            return str(series_id)
        return None

    def _parse_next_data(self, soup: BeautifulSoup) -> dict:
        script = soup.select_one("script#__NEXT_DATA__")
        if not script or not script.string:
            return {}

        try:
            return json.loads(script.string)
        except json.JSONDecodeError:
            return {}

    def _is_novel_page(self, next_data: dict) -> bool:
        canonical = next_data.get("props", {}).get("pageProps", {}).get("meta", {}).get("canonical", "")
        return "/novel/show.php" in canonical

    def _is_series_page(self, next_data: dict) -> bool:
        canonical = next_data.get("props", {}).get("pageProps", {}).get("meta", {}).get("canonical", "")
        return "/novel/series/" in canonical

    def _extract_title_value(self, payload: dict, key: str) -> str:
        value = payload.get(key)
        return str(value).strip() if value else ""

    def _build_novel_url(self, novel_id: str) -> str:
        return f"https://www.pixiv.net/novel/show.php?id={novel_id}"