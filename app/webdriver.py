from pathlib import Path

driver = None


def _windows_browser_path(browser: str) -> str | None:
    candidates = {
        "chrome": (
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ),
        "edge": (
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        ),
    }

    for candidate in candidates.get(browser, ()):
        if Path(candidate).exists():
            return candidate
    return None


def _choose_browser(browser: str) -> str:
    if browser != "auto":
        return browser
    if _windows_browser_path("chrome"):
        return "chrome"
    if _windows_browser_path("edge"):
        return "edge"
    return "chrome"


def _start_chrome_uc(headless: bool):
    from seleniumbase import Driver

    return Driver(
        browser="chrome",
        uc=True,
        headless2=headless,
        incognito=True,
        locale_code="ja-JP",
    )


def _start_edge_fallback(headless: bool):
    from selenium import webdriver as selenium_webdriver
    from selenium.webdriver.edge.options import Options

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


def minimize_webdriver() -> None:
    if driver:
        try:
            driver.minimize_window()
        except Exception:
            pass


def restore_webdriver() -> None:
    if driver:
        try:
            driver.maximize_window()
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
    elif selected_browser == "edge":
        print("Chrome UC mode is unavailable; using Edge fallback with stealth options.")
        driver = _start_edge_fallback(headless=headless)
    else:
        raise ValueError(f"Unsupported browser: {browser}")

    if not headless:
        minimize_webdriver()

    return driver


def close_webdriver():
    global driver
    if driver:
        print("Close WebDriver")
        driver.quit()
        driver = None