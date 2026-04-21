from __future__ import annotations

import importlib.util
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
    'a[href*="/episodes/"]',
    'a[href$=".html"]',
    "script#__NEXT_DATA__",
)

_CHAPTER_RECOGNITION_TIMEOUT = 10


class SeleniumFallbackError(RuntimeError):
    pass


def _load_local_webdriver_module():
    module_path = Path(__file__).resolve().parent / "webdriver.py"
    if not module_path.exists():
        raise SeleniumFallbackError(f"Selenium webdriver helper not found: {module_path}")

    spec = importlib.util.spec_from_file_location("noveltrans_selenium_webdriver", module_path)
    if spec is None or spec.loader is None:
        raise SeleniumFallbackError(f"Could not load Selenium webdriver helper: {module_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _is_challenge_page(driver) -> bool:
    title = driver.title or ""
    source = driver.page_source or ""
    return any(marker in title or marker in source for marker in _CHALLENGE_MARKERS)


def _wait_until_ready(driver, timeout: int, verify_timeout: int) -> None:
    from selenium.webdriver.support.ui import WebDriverWait

    WebDriverWait(driver, timeout).until(
        lambda current_driver: current_driver.execute_script("return document.readyState") == "complete"
    )

    if _is_challenge_page(driver):
        webdriver_module = _load_local_webdriver_module()
        webdriver_module.restore_webdriver()
        print("[INFO] Cloudflare verification page detected. Complete verification in the browser window.")
        WebDriverWait(driver, verify_timeout).until(lambda current_driver: not _is_challenge_page(current_driver))


def _has_recognizable_chapter_markup(driver) -> bool:
    return bool(
        driver.execute_script(
            """
const selectors = arguments[0];
return selectors.some((selector) => document.querySelector(selector));
            """,
            list(_CHAPTER_RECOGNITION_SELECTORS),
        )
    )


def _restore_if_chapter_recognition_is_slow(driver, timeout: int) -> None:
    from selenium.webdriver.support.ui import WebDriverWait

    if _has_recognizable_chapter_markup(driver):
        return

    webdriver_module = _load_local_webdriver_module()
    try:
        WebDriverWait(driver, timeout).until(lambda current_driver: _has_recognizable_chapter_markup(current_driver))
    except Exception:
        webdriver_module.restore_webdriver()
        print("[INFO] 10초 안에 챕터/본문을 인식하지 못해 Selenium 창을 복원했습니다.")


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
        _wait_until_ready(driver, self.ready_timeout, self.verify_timeout)
        _restore_if_chapter_recognition_is_slow(driver, _CHAPTER_RECOGNITION_TIMEOUT)
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
