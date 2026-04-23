from __future__ import annotations

import time
from typing import Protocol

from bs4 import BeautifulSoup

from app.extract.selenium import SeleniumPageFetcher
from app.settings.logging import log_runtime_event
from app.ui import render_wait_screen
from app.ui.control import parse_command, prompt_crawler_error_choice, prompt_crawler_retry_wait


class CrawlerFetchState(Protocol):
    error_mode: str
    session: object
    selenium_fetcher: SeleniumPageFetcher | None
    last_error_message: str | None

    def get_page(self, url: str, prompt_on_error: bool = True) -> BeautifulSoup | None:
        ...


def is_cloudflare_challenge(response) -> bool:
    text = response.text[:10000]
    return response.status_code in {403, 429, 503} and (
        "Just a moment..." in text
        or "challenges.cloudflare.com" in text
        or "cf-chl" in text
    )


def get_page_with_selenium(crawler: CrawlerFetchState, url: str, reason: str) -> BeautifulSoup | None:
    log_runtime_event(f"crawler selenium fallback start | url={url} | reason={reason}")
    print(f"[INFO] cloudscraper 차단 감지({reason}). Selenium fallback으로 재시도합니다.")

    try:
        if crawler.selenium_fetcher is None:
            crawler.selenium_fetcher = SeleniumPageFetcher(crawler.session)
        html = crawler.selenium_fetcher.fetch_html(url)
    except Exception as error:
        crawler.last_error_message = f"Selenium fallback 실패: {error}"
        log_runtime_event(f"crawler selenium fallback failed | url={url} | error={error!r}")
        print(f"[ERROR] Selenium fallback 실패: {error}")
        return None

    crawler.last_error_message = None
    log_runtime_event(f"crawler selenium fallback success | url={url} | bytes={len(html.encode('utf-8'))}")
    return BeautifulSoup(html, "html.parser")


def handle_page_error(
    crawler: CrawlerFetchState,
    url: str,
    error: Exception,
    prompt_on_error: bool,
) -> BeautifulSoup | None:
    crawler.last_error_message = str(error)
    log_runtime_event(f"crawler page blocked | url={url} | error={error!r}")
    print(f"[ERROR] 페이지 로드 실패: {error}")
    if prompt_on_error:
        return handle_interactive_error(crawler, url, error)
    return None


def handle_interactive_error(crawler: CrawlerFetchState, url: str, error: Exception) -> BeautifulSoup | None:
    if crawler.error_mode == "skip":
        print("         자동 스킵")
        return None
    if crawler.error_mode == "stop":
        raise error

    status_message = None
    while True:
        choice = prompt_crawler_error_choice(url, error, status_message=status_message)
        status_message = None

        if choice == "1":
            return None

        if choice == "2":
            retry_status_message = None
            while True:
                wait = prompt_crawler_retry_wait(url, error, status_message=retry_status_message)
                retry_status_message = None
                if parse_command(wait) == "back":
                    break

                try:
                    wait_time = float(wait) if wait else 5.0
                except ValueError:
                    retry_status_message = "숫자 또는 =만 입력해주세요."
                    continue

                render_wait_screen(wait_time)
                time.sleep(wait_time)
                return crawler.get_page(url)

        if choice == "3":
            crawler.error_mode = "skip"
            print("이후 오류는 자동으로 스킵합니다.")
            return None

        if choice == "4":
            raise KeyboardInterrupt("사용자가 작업을 중단했습니다.")

        status_message = "잘못된 선택입니다. 다시 입력해주세요."


__all__ = [
    "get_page_with_selenium",
    "handle_interactive_error",
    "handle_page_error",
    "is_cloudflare_challenge",
]