from __future__ import annotations

import random
import re
import time
from pathlib import Path
from urllib.parse import urljoin

import cloudscraper
from bs4 import BeautifulSoup

from app.settings import SOURCE_PATH
from app.ui import (
    clear_screen,
    parse_command,
    render_crawl_complete_screen,
    render_crawl_progress_screen,
    render_crawler_error_screen,
    render_crawler_screen,
)


OUTPUT_PATH = SOURCE_PATH
Chapter = tuple[int, str, str]


def _format_chapter_document(title: str, content: str) -> str:
    return f"{title}\n{'=' * 60}\n\n{content}"


def _format_combined_document(results: list[tuple[int, str, str]], novel_folder: str) -> str:
    sections = [f"{'=' * 60}\n{title}\n{'=' * 60}\n\n{content}" for _num, title, content in results]
    header = f"{novel_folder}\n{'=' * 60}\n총 {len(results)}화\n{'=' * 60}\n\n\n"
    return header + "\n\n\n".join(sections) + "\n"


def _filter_chapters(
    chapters: list[Chapter],
    start_chapter: int | None,
    end_chapter: int | None,
) -> list[Chapter]:
    return [
        chapter
        for chapter in chapters
        if (start_chapter is None or chapter[0] >= start_chapter)
        and (end_chapter is None or chapter[0] <= end_chapter)
    ]


def _parse_chapter_range(range_input: str) -> tuple[int | None, int | None, bool]:
    start_chapter = None
    end_chapter = None

    if not range_input:
        return start_chapter, end_chapter, True

    if "~" in range_input:
        parts = range_input.split("~")
    elif "-" in range_input:
        parts = range_input.split("-")
    else:
        parts = [range_input]

    try:
        if len(parts) == 2:
            start_chapter = int(parts[0].strip()) if parts[0].strip() else None
            end_chapter = int(parts[1].strip()) if parts[1].strip() else None
        elif len(parts) == 1:
            start_chapter = end_chapter = int(parts[0].strip())
        else:
            return None, None, False
    except ValueError:
        return None, None, False

    return start_chapter, end_chapter, True


class NovelCrawler:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/") + "/"
        self.session = cloudscraper.create_scraper(
            browser={
                "browser": "chrome",
                "platform": "windows",
                "desktop": True,
            }
        )
        self.session.cookies.set("over18", "yes", domain="syosetu.org")
        self.session.cookies.set("over18", "yes", domain=".syosetu.org")

        self.error_mode = "ask"
        self.retry_count = 3
        self.retry_delay = 3.0
        self.novel_title: str | None = None
        self.last_output_path: Path | None = None
        self.last_combined_path: Path | None = None
        self.last_failed_count = 0
        self.last_success_count = 0
        self.last_total_count = 0

    def get_page(self, url: str, prompt_on_error: bool = True) -> BeautifulSoup | None:
        for attempt in range(self.retry_count):
            try:
                if attempt > 0:
                    wait_time = self.retry_delay * (attempt + 1)
                    print(f"         재시도 {attempt + 1}/{self.retry_count} ({wait_time}초 대기)...")
                    time.sleep(wait_time)

                response = self.session.get(url, timeout=30)
                response.raise_for_status()
                response.encoding = response.apparent_encoding or "utf-8"
                return BeautifulSoup(response.text, "html.parser")
            except Exception as error:
                error_msg = str(error)
                if attempt < self.retry_count - 1 and ("403" in error_msg or "429" in error_msg):
                    continue

                print(f"[ERROR] 페이지 로드 실패: {error}")
                if prompt_on_error:
                    return self._handle_error(url, error)
                return None

        return None

    def _handle_error(self, url: str, error: Exception) -> BeautifulSoup | None:
        if self.error_mode == "skip":
            print("         자동 스킵")
            return None
        if self.error_mode == "stop":
            raise error

        status_message = None
        while True:
            render_crawler_error_screen(url, error, status_message=status_message)
            status_message = None
            choice = input("선택 (1/2/3/4): ").strip()

            if choice == "1":
                return None

            if choice == "2":
                retry_status_message = None
                while True:
                    render_crawler_error_screen(
                        url,
                        error,
                        status_message=retry_status_message,
                        waiting_for_retry=True,
                    )
                    retry_status_message = None
                    wait = input("대기 시간(초, 기본 5, 뒤로가기 /b): ").strip()
                    if parse_command(wait) == "back":
                        break

                    try:
                        wait_time = float(wait) if wait else 5.0
                    except ValueError:
                        retry_status_message = "숫자 또는 /b만 입력해 주세요."
                        continue

                    clear_screen()
                    print(f"{wait_time}초 대기 중...")
                    time.sleep(wait_time)
                    return self.get_page(url)

            if choice == "3":
                self.error_mode = "skip"
                print("이후 오류는 자동으로 스킵합니다.")
                return None

            if choice == "4":
                raise KeyboardInterrupt("사용자가 크롤링을 중단했습니다.")

            status_message = "잘못된 선택입니다. 다시 입력해 주세요."

    def get_chapter_links(self, prompt_on_error: bool = True) -> list[Chapter]:
        soup = self.get_page(self.base_url, prompt_on_error=prompt_on_error)
        if not soup:
            return []

        self.novel_title = self._extract_novel_title(soup)
        chapters: list[Chapter] = []

        for link in soup.find_all("a", href=True):
            href = link.get("href", "")
            match = re.search(r"(?:\./)?(\d+)\.html$", href)
            if not match:
                continue

            chapter_num = int(match.group(1))
            chapter_title = link.get_text(strip=True) or f"제{chapter_num}화"
            full_url = urljoin(self.base_url, href)
            chapters.append((chapter_num, chapter_title, full_url))

        seen: set[str] = set()
        unique_chapters: list[Chapter] = []
        for chapter in chapters:
            if chapter[2] in seen:
                continue
            seen.add(chapter[2])
            unique_chapters.append(chapter)

        unique_chapters.sort(key=lambda item: item[0])
        return unique_chapters

    def extract_content(self, soup: BeautifulSoup) -> str:
        if not soup:
            return ""

        selectors = [
            {"id": "honbun"},
            {"class_": "honbun"},
            {"id": "novel_honbun"},
            {"class_": "novel_view"},
            {"id": "novel_view"},
        ]

        body_element = None
        for selector in selectors:
            body_element = soup.find("div", **selector)
            if body_element:
                break

        if body_element:
            for br in body_element.find_all("br"):
                br.replace_with("\n")
            for p in body_element.find_all("p"):
                p.insert_after("\n")
            content = body_element.get_text()
        else:
            print("[WARNING] 본문 컨테이너를 찾지 못했습니다. 대체 방법을 시도합니다...")
            all_divs = soup.find_all("div")
            if not all_divs:
                return ""
            longest_div = max(all_divs, key=lambda div: len(div.get_text()))
            content = longest_div.get_text()

        return self.clean_text(content)

    def clean_text(self, text: str) -> str:
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        lines = [line.strip() for line in text.split("\n")]
        text = "\n".join(lines)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def get_chapter_title(self, soup: BeautifulSoup) -> str:
        if not soup:
            return ""

        title_selectors = [
            ("h1", {"class_": "novel_subtitle"}),
            ("h1", {}),
            ("div", {"class_": "novel_subtitle"}),
            ("span", {"class_": "novel_subtitle"}),
        ]

        for tag, attrs in title_selectors:
            element = soup.find(tag, **attrs)
            if element:
                return element.get_text(strip=True)
        return ""

    def crawl_all(
        self,
        delay: float = 1.5,
        start_chapter: int | None = None,
        end_chapter: int | None = None,
        chapters: list[Chapter] | None = None,
        output_dir: Path = OUTPUT_PATH,
    ) -> list[tuple[int, str, str]]:
        if chapters is None:
            print(f"[INFO] 소설 페이지 분석 중: {self.base_url}")
            print("[INFO] 메인 페이지 접속 중 (세션 초기화)...")
            main_soup = self.get_page(self.base_url)
            if not main_soup:
                print("[ERROR] 메인 페이지에 접속할 수 없습니다.")
                return []

            time.sleep(2)
            chapters = self.get_chapter_links()
            if not chapters:
                print("[ERROR] 챕터를 찾을 수 없습니다.")
                return []

        if start_chapter or end_chapter:
            chapters = _filter_chapters(chapters, start_chapter, end_chapter)

        save_path = self._get_novel_output_path(output_dir)
        self.last_output_path = save_path
        self.last_combined_path = None
        self.last_failed_count = 0
        self.last_success_count = 0
        self.last_total_count = len(chapters)

        results: list[tuple[int, str, str]] = []
        failed_chapters: list[tuple[int, str, str]] = []
        total = len(chapters)

        for index, (num, title, url) in enumerate(chapters, start=1):
            current_title = f"{num}화 {title[:40]}"
            render_crawl_progress_screen(
                chapters=chapters,
                current_index=index - 1,
                total=total,
                current_title=current_title,
                output_path=save_path,
                status_message="페이지를 불러오는 중...",
                failed_count=len(failed_chapters),
            )

            try:
                soup = self.get_page(url)
                if soup:
                    page_title = self.get_chapter_title(soup) or title
                    content = self.extract_content(soup)
                    if content:
                        results.append((num, page_title, content))
                        self._save_chapter_file(num, page_title, content, save_path)
                        status_message = f"[INFO] {num}화 저장 완료"
                    else:
                        failed_chapters.append((num, title, "본문 추출 실패"))
                        status_message = f"[WARNING] {num}화 본문 추출 실패"
                else:
                    failed_chapters.append((num, title, "페이지 로드 실패"))
                    status_message = f"[WARNING] {num}화 페이지 로드 실패"
            except KeyboardInterrupt:
                raise

            render_crawl_progress_screen(
                chapters=chapters,
                current_index=index,
                total=total,
                current_title=current_title,
                output_path=save_path,
                status_message=status_message,
                failed_count=len(failed_chapters),
            )

            if index < total:
                actual_delay = delay + random.uniform(0.5, 1.5)
                time.sleep(actual_delay)

        if results:
            self.last_combined_path = self._save_combined_file(
                results,
                save_path,
                self.novel_title or "unknown_novel",
            )

        self.last_failed_count = len(failed_chapters)
        self.last_success_count = len(results)
        return results

    def _extract_novel_title(self, soup: BeautifulSoup) -> str:
        if not soup:
            return "unknown_novel"

        title_selectors = [
            ("title", {}),
            ("h1", {}),
            ("meta", {"property": "og:title"}),
        ]

        for tag, attrs in title_selectors:
            if tag == "meta":
                element = soup.find(tag, attrs)
                if element and element.get("content"):
                    title = re.split(r"\s*[-|]\s*", element.get("content"))[0].strip()
                    if title:
                        return self._sanitize_filename(title)
                continue

            element = soup.find(tag, **attrs)
            if element:
                title = re.split(r"\s*[-|]\s*", element.get_text(strip=True))[0].strip()
                if title:
                    return self._sanitize_filename(title)

        return "unknown_novel"

    def _sanitize_filename(self, filename: str) -> str:
        filename = re.sub(r'[<>:"/\\|?*]', "", filename)
        filename = filename.strip(". ")
        if len(filename) > 100:
            filename = filename[:100]
        return filename or "unknown_novel"

    def _get_novel_output_path(self, output_dir: Path) -> Path:
        novel_folder = self.novel_title or "unknown_novel"
        full_path = output_dir / novel_folder
        full_path.mkdir(parents=True, exist_ok=True)
        return full_path

    def _save_chapter_file(self, num: int, title: str, content: str, output_path: Path) -> None:
        filepath = output_path / f"{num:04d}.txt"
        filepath.write_text(_format_chapter_document(title, content), encoding="utf-8")

    def _save_combined_file(
        self,
        results: list[tuple[int, str, str]],
        output_path: Path,
        novel_folder: str,
    ) -> Path:
        start_num = min(num for num, _, _ in results)
        end_num = max(num for num, _, _ in results)
        combined_path = output_path / f"({start_num:04d}~{end_num:04d}){novel_folder}.txt"
        combined_path.write_text(_format_combined_document(results, novel_folder), encoding="utf-8")
        return combined_path


def main() -> int:
    try:
        crawler: NovelCrawler | None = None
        chapters: list[Chapter] = []
        founded: list[Chapter] = []
        start_chapter: int | None = None
        end_chapter: int | None = None
        step = "url"
        status_message = None

        while True:
            if step == "url":
                render_crawler_screen(step, status_message, founded)
                novel_url = input("").strip()
                command = parse_command(novel_url)

                if command in {"main", "back"}:
                    return 0
                if command == "exit":
                    return 130

                if not novel_url:
                    status_message = "[ERROR] URL을 입력해 주세요."
                    continue

                status_message = "[RUN] URL 분석 중..."
                render_crawler_screen(step, status_message, founded)
                crawler = NovelCrawler(novel_url)
                chapters = crawler.get_chapter_links(prompt_on_error=False)
                start_chapter = None
                end_chapter = None

                if not chapters:
                    status_message = "[ERROR] 챕터를 찾을 수 없습니다. URL을 다시 확인해 주세요."
                    continue

                step = "range"
                status_message = None
                founded = chapters
                continue

            if step == "range":
                render_crawler_screen(step, status_message, founded)
                range_input = input("").strip()
                command = parse_command(range_input)

                if command == "main":
                    return 0
                if command == "back":
                    step = "url"
                    status_message = None
                    continue
                if command == "exit":
                    return 130

                start_chapter, end_chapter, is_valid = _parse_chapter_range(range_input)
                if not is_valid:
                    status_message = "[ERROR] 범위 형식이 잘못되었습니다. 다시 입력해 주세요."
                    continue

                filtered = _filter_chapters(chapters, start_chapter, end_chapter)
                if not filtered:
                    status_message = "[ERROR] 입력한 범위에 해당하는 챕터가 없습니다."
                    continue

                step = "delay"
                status_message = None
                founded = filtered
                continue

            if step == "delay":
                render_crawler_screen(step, status_message, founded)
                delay_input = input("").strip()
                command = parse_command(delay_input)

                if command == "main":
                    return 0
                if command == "back":
                    step = "range"
                    status_message = None
                    founded = chapters
                    continue
                if command == "exit":
                    return 130

                try:
                    delay = float(delay_input) if delay_input else 1.0
                except ValueError:
                    status_message = "[ERROR] 요청 간격은 숫자로 입력해 주세요."
                    continue

                try:
                    assert crawler is not None
                    crawler.crawl_all(
                        delay=delay,
                        start_chapter=start_chapter,
                        end_chapter=end_chapter,
                        chapters=founded,
                        output_dir=OUTPUT_PATH,
                    )
                    render_crawl_complete_screen(
                        total=crawler.last_total_count,
                        success_count=crawler.last_success_count,
                        failed_count=crawler.last_failed_count,
                        output_path=crawler.last_output_path or OUTPUT_PATH,
                    )
                    input("")
                except Exception as error:
                    print(f"\n[ERROR] 크롤링 중 오류 발생: {error}")
                    return 1

                return 0

        return 0
    except KeyboardInterrupt:
        print("\n[INFO] 사용자가 작업을 중단했습니다.")
        return 130
    except Exception as exc:
        print(f"[ERROR] {exc}")
        return 1
