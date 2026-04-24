from __future__ import annotations

import random
import time
import traceback
from pathlib import Path

import cloudscraper
from bs4 import BeautifulSoup

from app.extract import fetch as fetch_helpers
from app.settings.config import get_runtime_settings
from app.settings.logging import log_runtime_event
from app.extract.storage import get_novel_output_path, save_chapter_file
from app.ui.control import (
    parse_command,
    prompt_crawler_screen,
    wait_for_enter,
)
from app.extract.selenium import SeleniumPageFetcher
from app.extract.site import Chapter, resolve_extractor
from app.ui import (
    render_crawl_complete_screen,
    render_crawl_progress_screen,
    render_crawler_screen,
)
from app.utils import parse_chapter_selection


OUTPUT_PATH = get_runtime_settings().source_path

_MIN_DELAY_SECONDS = 0.3
_DELAY_JITTER_RATIO = 0.25
_DELAY_EXTRA_PAUSE_CHANCE = 0.12
_DELAY_EXTRA_PAUSE_RANGE = (0.8, 2.4)


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


def _build_request_delay(base_delay: float) -> float:
    safe_delay = max(_MIN_DELAY_SECONDS, base_delay)
    jitter = safe_delay * _DELAY_JITTER_RATIO
    randomized_delay = random.uniform(max(_MIN_DELAY_SECONDS, safe_delay - jitter), safe_delay + jitter)

    if random.random() < _DELAY_EXTRA_PAUSE_CHANCE:
        randomized_delay += random.uniform(*_DELAY_EXTRA_PAUSE_RANGE)

    return randomized_delay


class NovelCrawler:
    def __init__(self, base_url: str):
        self.extractor = resolve_extractor(base_url)
        self.base_url = self.extractor.normalize_base_url(base_url)
        log_runtime_event(
            f"crawler init | input_url={base_url} | normalized_url={self.base_url} | site={self.extractor.site_name}"
        )
        self.session = cloudscraper.create_scraper(
            browser={
                "browser": "chrome",
                "platform": "windows",
                "desktop": True,
            }
        )
        prepared_session = self.extractor.prepare_session(self.session)
        if prepared_session is not None:
            self.session = prepared_session

        self.error_mode = "ask"
        self.retry_count = 3
        self.retry_delay = 3.0
        self.novel_title: str | None = None
        self.selenium_fetcher: SeleniumPageFetcher | None = None
        self.last_output_path: Path | None = None
        self.last_failed_count = 0
        self.last_success_count = 0
        self.last_total_count = 0
        self.last_error_message: str | None = None

    def close(self) -> None:
        if self.selenium_fetcher is not None:
            self.selenium_fetcher.close()
            self.selenium_fetcher = None

    def get_page(self, url: str, prompt_on_error: bool = True) -> BeautifulSoup | None:
        log_runtime_event(f"crawler page request start | url={url}")
        for attempt in range(self.retry_count):
            try:
                if attempt > 0:
                    wait_time = self.retry_delay * (attempt + 1)
                    print(f"         재시도 {attempt + 1}/{self.retry_count} ({wait_time}초 대기)...")
                    time.sleep(wait_time)

                response = self.session.get(url, timeout=30)
                response.encoding = response.apparent_encoding or "utf-8"
                if fetch_helpers.is_cloudflare_challenge(response):
                    fallback_soup = self._get_page_with_selenium(
                        url,
                        reason=f"cloudflare challenge ({response.status_code})",
                    )
                    if fallback_soup is not None:
                        return fallback_soup
                    error = RuntimeError("403 Forbidden: Cloudflare 차단 페이지를 받았습니다.")
                    return self._handle_page_error(url, error, prompt_on_error)
                if response.status_code == 403:
                    fallback_soup = self._get_page_with_selenium(url, reason="403 forbidden")
                    if fallback_soup is not None:
                        return fallback_soup
                    error = RuntimeError("403 Forbidden: 사이트가 자동 추출 요청을 거부했습니다.")
                    return self._handle_page_error(url, error, prompt_on_error)
                response.raise_for_status()
                self.last_error_message = None
                log_runtime_event(
                    f"crawler page request success | url={url} | status={response.status_code} | "
                    f"bytes={len(response.content)} | attempt={attempt + 1}/{self.retry_count}"
                )
                return BeautifulSoup(response.text, "html.parser")
            except Exception as error:
                self.last_error_message = str(error)
                log_runtime_event(
                    f"crawler page request failed | url={url} | attempt={attempt + 1}/{self.retry_count} | "
                    f"error={error!r}"
                )
                error_msg = str(error)
                if attempt < self.retry_count - 1 and ("403" in error_msg or "429" in error_msg):
                    continue

                print(f"[ERROR] 페이지 로드 실패: {error}")
                if prompt_on_error:
                    return self._handle_error(url, error)
                return None

        return None

    def _handle_page_error(
        self,
        url: str,
        error: Exception,
        prompt_on_error: bool,
    ) -> BeautifulSoup | None:
        self.last_error_message = str(error)
        log_runtime_event(f"crawler page blocked | url={url} | error={error!r}")
        print(f"[ERROR] 페이지 로드 실패: {error}")
        if prompt_on_error:
            return self._handle_error(url, error)
        return None

    def _get_page_with_selenium(self, url: str, reason: str) -> BeautifulSoup | None:
        log_runtime_event(f"crawler selenium fallback start | url={url} | reason={reason}")
        print(f"[INFO] cloudscraper 차단 감지({reason}). Selenium fallback으로 재시도합니다.")

        try:
            if self.selenium_fetcher is None:
                self.selenium_fetcher = SeleniumPageFetcher(self.session)
            html = self.selenium_fetcher.fetch_html(url)
        except Exception as error:
            fetch_helpers.reset_selenium_fallback(self)
            self.last_error_message = f"Selenium fallback 실패: {error}"
            log_runtime_event(f"crawler selenium fallback failed | url={url} | error={error!r}")
            print(f"[ERROR] Selenium fallback 실패: {error}")
            return None

        self.last_error_message = None
        log_runtime_event(f"crawler selenium fallback success | url={url} | bytes={len(html.encode('utf-8'))}")
        return BeautifulSoup(html, "html.parser")

    def _handle_error(self, url: str, error: Exception) -> BeautifulSoup | None:
        return fetch_helpers.handle_interactive_error(self, url, error)

    def get_chapter_links(self, prompt_on_error: bool = True) -> list[Chapter]:
        soup = self.get_page(self.base_url, prompt_on_error=prompt_on_error)
        if not soup:
            return []

        self.novel_title = self.extractor.extract_novel_title(soup)
        return self.extractor.extract_chapter_links(self.base_url, soup, self.get_page)

    def extract_content(self, soup: BeautifulSoup) -> str:
        if not soup:
            return ""
        return self.extractor.extract_content(soup)

    def get_chapter_title(self, soup: BeautifulSoup) -> str:
        if not soup:
            return ""
        return self.extractor.extract_chapter_title(soup)

    def crawl_all(
        self,
        delay: float = 1.5,
        start_chapter: int | None = None,
        end_chapter: int | None = None,
        chapters: list[Chapter] | None = None,
        output_dir: Path = OUTPUT_PATH,
    ) -> list[tuple[int, str, str]]:
        if chapters is None:
            log_runtime_event(
                f"crawler crawl start | url={self.base_url} | delay={delay} | "
                f"start={start_chapter} | end={end_chapter}"
            )
            print(f"[INFO] 소설 페이지 분석 중: {self.base_url}")
            print("[INFO] 메인 페이지 접속 중...")
            main_soup = self.get_page(self.base_url)
            if not main_soup:
                print("[ERROR] 메인 페이지에 접속할 수 없습니다.")
                return []

            time.sleep(2)
            chapters = self.get_chapter_links()
            if not chapters:
                log_runtime_event(f"crawler crawl aborted | url={self.base_url} | reason=no_chapters")
                print("[ERROR] 챕터를 찾을 수 없습니다.")
                return []
        else:
            log_runtime_event(
                f"crawler crawl start | url={self.base_url} | delay={delay} | "
                f"start={start_chapter} | end={end_chapter} | provided_chapters={len(chapters)}"
            )

        if start_chapter or end_chapter:
            chapters = _filter_chapters(chapters, start_chapter, end_chapter)

        save_path = get_novel_output_path(self.novel_title, output_dir)
        self.last_output_path = save_path
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
                        save_chapter_file(num, page_title, content, save_path)
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
                actual_delay = _build_request_delay(delay)
                log_runtime_event(
                    f"crawler inter-request delay | chapter={num} | base_delay={delay:.2f} | actual_delay={actual_delay:.2f}"
                )
                time.sleep(actual_delay)

        self.last_failed_count = len(failed_chapters)
        self.last_success_count = len(results)
        log_runtime_event(
            f"crawler crawl complete | url={self.base_url} | total={total} | "
            f"success={self.last_success_count} | failed={self.last_failed_count} | output_dir={save_path}"
        )
        return results

    


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
                novel_url = prompt_crawler_screen(step, status_message, founded)
                command = parse_command(novel_url)

                if command == "back":
                    if crawler is not None:
                        crawler.close()
                    return 0
                if command == "exit":
                    if crawler is not None:
                        crawler.close()
                    return 130

                if not novel_url:
                    status_message = "[ERROR] URL을 입력해주세요."
                    continue

                status_message = "[RUN] URL 분석 중..."
                render_crawler_screen(step, status_message, founded)
                try:
                    crawler = NovelCrawler(novel_url)
                except ValueError as error:
                    log_runtime_event(f"crawler url rejected | url={novel_url} | error={error!r}")
                    status_message = f"[ERROR] {error}"
                    continue

                chapters = crawler.get_chapter_links(prompt_on_error=False)
                start_chapter = None
                end_chapter = None

                if not chapters:
                    if crawler.last_error_message:
                        status_message = f"[ERROR] {crawler.last_error_message}"
                    else:
                        status_message = "[ERROR] 챕터를 찾을 수 없습니다. URL을 다시 확인해주세요."
                    continue

                step = "range"
                status_message = None
                founded = chapters
                continue

            if step == "range":
                range_input = prompt_crawler_screen(step, status_message, founded)
                command = parse_command(range_input)

                if command == "back":
                    if crawler is not None:
                        crawler.close()
                    step = "url"
                    status_message = None
                    continue
                if command == "exit":
                    if crawler is not None:
                        crawler.close()
                    return 130

                if not range_input:
                    start_chapter, end_chapter = None, None
                else:
                    selection = parse_chapter_selection(range_input)
                    if selection is None:
                        status_message = "[ERROR] 범위 형식이 잘못되었습니다. 다시 입력해주세요."
                        continue
                    start_chapter, end_chapter = selection

                if start_chapter is not None and end_chapter is not None and start_chapter > end_chapter:
                    status_message = "[ERROR] 범위 형식이 잘못되었습니다. 다시 입력해주세요."
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
                delay_input = prompt_crawler_screen(step, status_message, founded)
                command = parse_command(delay_input)

                if command == "back":
                    if crawler is not None:
                        crawler.close()
                    step = "range"
                    status_message = None
                    founded = chapters
                    continue
                if command == "exit":
                    if crawler is not None:
                        crawler.close()
                    return 130

                try:
                    delay = float(delay_input) if delay_input else 1.0
                except ValueError:
                    status_message = "[ERROR] 요청 간격은 숫자로 입력해주세요."
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
                    wait_for_enter()
                except Exception as error:
                    log_runtime_event(f"crawler run failed | error={error!r}\n{traceback.format_exc()}")
                    print(f"\n[ERROR] 크롤링 중 오류 발생: {error}")
                    return 1

                return 0

        return 0
    except KeyboardInterrupt:
        log_runtime_event("crawler cancelled by user")
        print("\n[INFO] 사용자가 작업을 중단했습니다.")
        return 130
    except Exception as exc:
        log_runtime_event(f"crawler main failed | error={exc!r}\n{traceback.format_exc()}")
        print(f"[ERROR] {exc}")
        return 1
    finally:
        if crawler is not None:
            crawler.close()