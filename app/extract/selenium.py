from __future__ import annotations

import importlib
import app.extract.webdriver as app_webdriver
import sys
import time
from pathlib import Path


_CHALLENGE_MARKERS = (
    "Just a moment",
    "Enable JavaScript and cookies",
    "Cloudflare",
    "cf-chl",
    "challenges.cloudflare.com",
    "\u3057\u3070\u3089\u304f\u304a\u5f85\u3061\u304f\u3060\u3055\u3044",
    "\u30bb\u30ad\u30e5\u30ea\u30c6\u30a3\u691c\u8a3c",
)

_CHAPTER_RECOGNITION_SELECTORS = (
    "#honbun",
    ".honbun",
    "#novel_honbun",
    ".novel_view",
    "#novel_view",
    ".widget-episodeBody",
    ".p-novel__body",
    ".js-novel-text",
    ".p-eplist__sublist a",
    'a[href*="/txt/"]',
    'a[href*="/episodes/"]',
    'a[href$=".html"]',
    "script#__NEXT_DATA__",
)

_CHAPTER_RECOGNITION_TIMEOUT = 10


class SeleniumFallbackError(RuntimeError):
    pass


def _import_external_selenium_module(module_name: str):
    current_module_path = Path(__file__).resolve()
    app_dir = current_module_path.parent
    removed_paths: list[tuple[int, str]] = []

    selenium_module = sys.modules.get("selenium")
    selenium_module_path = Path(getattr(selenium_module, "__file__", "")).resolve() if getattr(selenium_module, "__file__", "") else None
    is_shadowed = selenium_module_path == current_module_path or (
        selenium_module_path is not None and selenium_module_path.parent == app_dir
    )

    if is_shadowed:
        for loaded_name in list(sys.modules):
            if loaded_name == "selenium" or loaded_name.startswith("selenium."):
                del sys.modules[loaded_name]

    for index in range(len(sys.path) - 1, -1, -1):
        entry = sys.path[index]
        try:
            resolved_entry = Path(entry or ".").resolve()
        except OSError:
            continue
        if resolved_entry == app_dir:
            removed_paths.append((index, entry))
            del sys.path[index]

    try:
        return importlib.import_module(module_name)
    finally:
        for index, entry in reversed(removed_paths):
            sys.path.insert(index, entry)


def _load_local_webdriver_module():
    return app_webdriver


def _is_challenge_page(driver) -> bool:
    title = driver.title or ""
    source = driver.page_source or ""
    return any(marker in title or marker in source for marker in _CHALLENGE_MARKERS)


def _wait_for_cloudflare_verification(driver, verify_timeout: int) -> None:
    webdriver_module = _load_local_webdriver_module()
    WebDriverWait = _import_external_selenium_module("selenium.webdriver.support.ui").WebDriverWait

    print("[INFO] Cloudflare verification page detected.")
    webdriver_module.restore_webdriver()

    try:
        WebDriverWait(driver, verify_timeout).until(lambda current_driver: not _is_challenge_page(current_driver))
    except Exception:
        webdriver_module.restore_webdriver()
        raise

    time.sleep(0.5)
    webdriver_module.minimize_webdriver()


def _wait_until_ready(driver, timeout: int, verify_timeout: int) -> None:
    WebDriverWait = _import_external_selenium_module("selenium.webdriver.support.ui").WebDriverWait

    WebDriverWait(driver, timeout).until(
        lambda current_driver: current_driver.execute_script("return document.readyState") == "complete"
    )

    if _is_challenge_page(driver):
        _wait_for_cloudflare_verification(driver, verify_timeout)


def _has_recognizable_chapter_markup(driver) -> bool:
    return bool(
        driver.execute_script(
            """
const selectors = arguments[0];
if (selectors.some((selector) => document.querySelector(selector))) {
    return true;
}

const url = window.location.href || '';
if (url.includes('twkan.com/txt/')) {
    const title = document.querySelector('h1');
    const bodyText = (document.body && document.body.innerText) ? document.body.innerText : '';
    if (title && bodyText.length >= 800) {
        return true;
    }
}

return false;
            """,
            list(_CHAPTER_RECOGNITION_SELECTORS),
        )
    )


def _wait_for_chapter_recognition(driver, timeout: int) -> None:
    WebDriverWait = _import_external_selenium_module("selenium.webdriver.support.ui").WebDriverWait

    if _has_recognizable_chapter_markup(driver):
        return

    webdriver_module = _load_local_webdriver_module()
    try:
        WebDriverWait(driver, timeout).until(lambda current_driver: _has_recognizable_chapter_markup(current_driver))
    except Exception:
        webdriver_module.restore_webdriver()


def _expand_twkan_catalog_if_needed(driver, url: str, timeout: int) -> None:
    if not ("twkan.com/book/" in url and url.rstrip("/").endswith("/index.html")):
        return

    WebDriverWait = _import_external_selenium_module("selenium.webdriver.support.ui").WebDriverWait

    initial_count = driver.execute_script(
        "return document.querySelectorAll('#allchapter a[href*=\"/txt/\"]').length;"
    )
    if initial_count >= 100:
        return

    has_load_more = driver.execute_script(
        "return typeof LoadMore === 'function' && !!document.querySelector('#loadmore');"
    )
    if not has_load_more:
        return

    driver.execute_script("LoadMore();")
    WebDriverWait(driver, timeout).until(
        lambda current_driver: current_driver.execute_script(
            """
const chapterCount = document.querySelectorAll('#allchapter a[href*="/txt/"]').length;
const loadMore = document.querySelector('#loadmore');
const hidden = !loadMore || loadMore.offsetParent === null || loadMore.style.display === 'none';
return chapterCount > arguments[0] && hidden;
            """,
            initial_count,
        )
    )


def _sync_cookies_to_session(driver, session) -> None:
    for cookie in driver.get_cookies():
        name = cookie.get("name")
        value = cookie.get("value")
        if not name or value is None:
            continue
        cookie_kwargs = {"path": cookie.get("path", "/")}
        if cookie.get("domain"):
            cookie_kwargs["domain"] = cookie["domain"]
        session.cookies.set(name, value, **cookie_kwargs)


class SeleniumPageFetcher:
    def __init__(
        self,
        session,
        browser: str = "auto",
        headless: bool = False,
        ready_timeout: int = 60,
        verify_timeout: int = 180,
    ):
        self.session = session
        self.browser = browser
        self.headless = headless
        self.ready_timeout = ready_timeout
        self.verify_timeout = verify_timeout
        self._webdriver_module = None

    def fetch_html(self, url: str) -> str:
        if self._webdriver_module is None:
            self._webdriver_module = _load_local_webdriver_module()

        driver = self._webdriver_module.start_webdriver(
            browser=self.browser,
            headless=self.headless,
        )
        driver.get(url)
        if not self.headless:
            self._webdriver_module.minimize_webdriver()
        _wait_until_ready(driver, self.ready_timeout, self.verify_timeout)
        _expand_twkan_catalog_if_needed(driver, url, _CHAPTER_RECOGNITION_TIMEOUT)
        _wait_for_chapter_recognition(driver, _CHAPTER_RECOGNITION_TIMEOUT)
        time.sleep(0.5)
        html = driver.page_source or ""
        if _is_challenge_page(driver):
            raise SeleniumFallbackError(f"Selenium could not pass the challenge page: {url}")
        if not html.strip():
            raise SeleniumFallbackError(f"Selenium returned an empty page: {url}")

        _sync_cookies_to_session(driver, self.session)
        return html

    def close(self) -> None:
        if self._webdriver_module is not None:
            self._webdriver_module.close_webdriver()
            self._webdriver_module = None