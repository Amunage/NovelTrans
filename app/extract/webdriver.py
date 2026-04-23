import importlib
import sys
import time
from pathlib import Path

driver = None

_RESTORED_WINDOW_SIZE = (960, 720)
_RESTORED_WINDOW_POSITION = (80, 80)


def _import_external_selenium_module(module_name: str):
    app_dir = Path(__file__).resolve().parent
    local_shadow_path = app_dir / "selenium.py"
    removed_paths: list[tuple[int, str]] = []

    selenium_module = sys.modules.get("selenium")
    selenium_module_path = Path(getattr(selenium_module, "__file__", "")).resolve() if getattr(selenium_module, "__file__", "") else None
    is_shadowed = selenium_module_path == local_shadow_path or (
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


def _windows_browser_path(browser: str) -> str | None:
    candidates = {
        "chrome": (
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ),
        "whale": (
            r"C:\Program Files\Naver\Naver Whale\Application\whale.exe",
            r"C:\Program Files (x86)\Naver\Naver Whale\Application\whale.exe",
            r"C:\Users\%USERNAME%\AppData\Local\Naver\Naver Whale\Application\whale.exe",
        ),
        "edge": (
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        ),
    }

    for candidate in candidates.get(browser, ()):
        resolved_candidate = Path(candidate.replace("%USERNAME%", Path.home().name)).expanduser()
        if resolved_candidate.exists():
            return str(resolved_candidate)
    return None


def _choose_browser(browser: str) -> str:
    if browser != "auto":
        return browser
    if _windows_browser_path("chrome"):
        return "chrome"
    if _windows_browser_path("whale"):
        return "whale"
    if _windows_browser_path("edge"):
        return "edge"
    return "chrome"


def _start_chrome_uc(headless: bool, browser_path: str | None = None):
    from seleniumbase import Driver

    driver_kwargs = {
        "browser": "chrome",
        "uc": True,
        "headless2": headless,
        "incognito": True,
        "locale_code": "ja-JP",
    }
    if browser_path:
        driver_kwargs["binary_location"] = browser_path

    return Driver(**driver_kwargs)


def _start_edge_fallback(headless: bool):
    selenium_webdriver = _import_external_selenium_module("selenium.webdriver")
    Options = _import_external_selenium_module("selenium.webdriver.edge.options").Options

    options = Options()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-infobars")
    options.add_argument("--lang=ja-JP")
    options.add_argument("--window-size=1280,900")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    edge_path = _windows_browser_path("edge")
    if edge_path:
        options.binary_location = edge_path

    edge_driver = selenium_webdriver.Edge(options=options)
    edge_driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {
            "source": """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'languages', { get: () => ['ja-JP', 'ja', 'en-US', 'en'] });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
window.chrome = window.chrome || { runtime: {} };
            """,
        },
    )
    return edge_driver


def minimize_webdriver(attempts: int = 5, delay_seconds: float = 0.2) -> None:
    if driver:
        last_error = None
        for attempt in range(attempts):
            try:
                driver.minimize_window()
                return
            except Exception as exc:
                last_error = exc
                if attempt < attempts - 1:
                    time.sleep(delay_seconds)

        try:
            driver.set_window_position(-32000, -32000)
            driver.set_window_size(200, 200)
        except Exception:
            if last_error is not None:
                print(f"[WARNING] Selenium 창 최소화 실패: {last_error}")


def restore_webdriver() -> None:
    if driver:
        try:
            driver.set_window_position(*_RESTORED_WINDOW_POSITION)
            driver.set_window_size(*_RESTORED_WINDOW_SIZE)
        except Exception:
            pass


def start_webdriver(browser: str = "auto", headless: bool = False):
    global driver
    if driver:
        return driver

    selected_browser = _choose_browser(browser)
    print(f"Start WebDriver: {selected_browser}, headless={headless}")

    if selected_browser == "chrome":
        driver = _start_chrome_uc(headless=headless)
    elif selected_browser == "whale":
        whale_path = _windows_browser_path("whale")
        if not whale_path:
            raise ValueError("Naver Whale browser executable not found.")
        print("Using Naver Whale with Chrome UC mode.")
        driver = _start_chrome_uc(headless=headless, browser_path=whale_path)
    elif selected_browser == "edge":
        print("Chrome UC mode is unavailable; using Edge fallback with stealth options.")
        driver = _start_edge_fallback(headless=headless)
    else:
        raise ValueError(f"Unsupported browser: {browser}")

    return driver


def close_webdriver():
    global driver
    if driver:
        print("Close WebDriver")
        driver.quit()
        driver = None