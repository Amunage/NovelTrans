import atexit
import contextlib
import importlib
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from app.settings.logging import log_runtime_event

driver = None

_RESTORED_WINDOW_SIZE = (960, 720)
_RESTORED_WINDOW_POSITION = (80, 80)
_TRACKED_BROWSER_PIDS: set[int] = set()
_TRACKED_USER_DATA_DIRS: set[str] = set()


def _cleanup_webdriver_on_exit() -> None:
    close_webdriver()


def _kill_process_tree(pid: int | None) -> None:
    if pid is None or pid <= 0:
        return

    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return

        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass


def _list_browser_processes_by_user_data_dir(user_data_dir: str) -> list[int]:
    if os.name != "nt" or not user_data_dir:
        return []

    escaped_dir = user_data_dir.replace("'", "''")
    command = (
        "Get-CimInstance Win32_Process | "
        "Where-Object { $_.Name -in @('msedge.exe','chrome.exe','whale.exe') -and $_.CommandLine -like '*"
        + escaped_dir
        + "*' } | Select-Object -ExpandProperty ProcessId | ConvertTo-Json -Compress"
    )
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            capture_output=True,
            text=True,
            check=False,
        )
        output = completed.stdout.strip()
        if not output:
            return []

        import json

        parsed = json.loads(output)
        if isinstance(parsed, int):
            return [parsed]
        if isinstance(parsed, list):
            return [pid for pid in parsed if isinstance(pid, int) and pid > 0]
    except Exception as error:
        log_runtime_event(f"webdriver process scan failed | user_data_dir={user_data_dir} | error={error!r}")

    return []


def _get_webdriver_browser_metadata(webdriver_instance) -> tuple[int | None, str | None]:
    browser_pid = None
    user_data_dir = None

    try:
        direct_browser_pid = getattr(webdriver_instance, "browser_pid", None)
        if isinstance(direct_browser_pid, int) and direct_browser_pid > 0:
            browser_pid = direct_browser_pid
    except Exception:
        pass

    try:
        capabilities = getattr(webdriver_instance, "capabilities", None) or {}
    except Exception:
        capabilities = {}

    if browser_pid is None:
        capability_browser_pid = capabilities.get("goog:processID")
        if isinstance(capability_browser_pid, int) and capability_browser_pid > 0:
            browser_pid = capability_browser_pid

    for key in ("msedge", "chrome"):
        value = capabilities.get(key)
        if isinstance(value, dict):
            candidate_user_data_dir = value.get("userDataDir")
            if isinstance(candidate_user_data_dir, str) and candidate_user_data_dir:
                user_data_dir = candidate_user_data_dir
                break

    return browser_pid, user_data_dir


def _clear_tracked_webdriver_processes() -> None:
    _TRACKED_BROWSER_PIDS.clear()
    _TRACKED_USER_DATA_DIRS.clear()


def _cleanup_tracked_windows_browser_processes(
    browser_pids: set[int],
    user_data_dirs: set[str],
    service_pid: int | None,
) -> None:
    for tracked_pid in browser_pids:
        _kill_process_tree(tracked_pid)
    for tracked_user_data_dir in user_data_dirs:
        for matched_pid in _list_browser_processes_by_user_data_dir(tracked_user_data_dir):
            _kill_process_tree(matched_pid)
    if service_pid is not None:
        _kill_process_tree(service_pid)


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

    with open(os.devnull, "w", encoding="utf-8") as devnull:
        with contextlib.redirect_stdout(devnull), contextlib.redirect_stderr(devnull):
            return Driver(**driver_kwargs)


def _start_edge_fallback(headless: bool):
    selenium_webdriver = _import_external_selenium_module("selenium.webdriver")
    Options = _import_external_selenium_module("selenium.webdriver.edge.options").Options
    Service = _import_external_selenium_module("selenium.webdriver.edge.service").Service

    options = Options()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-infobars")
    options.add_argument("--lang=ja-JP")
    options.add_argument("--log-level=3")
    options.add_argument("--window-size=1280,900")
    options.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])
    options.add_experimental_option("useAutomationExtension", False)

    edge_path = _windows_browser_path("edge")
    if edge_path:
        options.binary_location = edge_path

    service = Service(log_output=subprocess.DEVNULL)
    edge_driver = selenium_webdriver.Edge(service=service, options=options)
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

    if selected_browser == "chrome":
        driver = _start_chrome_uc(headless=headless)
    elif selected_browser == "whale":
        whale_path = _windows_browser_path("whale")
        if not whale_path:
            raise ValueError("Naver Whale browser executable not found.")
        driver = _start_chrome_uc(headless=headless, browser_path=whale_path)
    elif selected_browser == "edge":
        driver = _start_edge_fallback(headless=headless)
    else:
        raise ValueError(f"Unsupported browser: {browser}")

    _track_webdriver_processes(driver)
    return driver


def _get_webdriver_service_pid(webdriver_instance) -> int | None:
    try:
        service_process = getattr(getattr(webdriver_instance, "service", None), "process", None)
        return getattr(service_process, "pid", None)
    except Exception:
        return None


def _track_webdriver_processes(webdriver_instance) -> None:
    browser_pid, user_data_dir = _get_webdriver_browser_metadata(webdriver_instance)

    if browser_pid is not None:
        _TRACKED_BROWSER_PIDS.add(browser_pid)
    if user_data_dir:
        _TRACKED_USER_DATA_DIRS.add(user_data_dir)


def _quit_webdriver_with_timeout(webdriver_instance, timeout_seconds: float = 2.0) -> tuple[bool, BaseException | None]:
    completion_event = threading.Event()
    result: dict[str, BaseException | None] = {"error": None}

    def _runner() -> None:
        try:
            webdriver_instance.quit()
        except BaseException as error:
            result["error"] = error
        finally:
            completion_event.set()

    quit_thread = threading.Thread(target=_runner, name="webdriver-quit", daemon=True)
    quit_thread.start()
    finished = completion_event.wait(timeout_seconds)
    if not finished:
        return False, None
    return True, result["error"]


def close_webdriver():
    global driver
    current_driver = driver
    if current_driver is None:
        return

    driver = None
    service_pid = _get_webdriver_service_pid(current_driver)
    browser_pid, user_data_dir = _get_webdriver_browser_metadata(current_driver)
    tracked_browser_pids = set(_TRACKED_BROWSER_PIDS)
    tracked_user_data_dirs = set(_TRACKED_USER_DATA_DIRS)

    if browser_pid is not None:
        tracked_browser_pids.add(browser_pid)
    if user_data_dir:
        tracked_user_data_dirs.add(user_data_dir)

    if os.name == "nt" and browser_pid is not None:
        log_runtime_event(
            f"webdriver close forced on Windows | browser_pid={browser_pid} | service_pid={service_pid} | user_data_dir={user_data_dir}"
        )
        _cleanup_tracked_windows_browser_processes(tracked_browser_pids, tracked_user_data_dirs, service_pid)
        _clear_tracked_webdriver_processes()
        return

    quit_finished, quit_error = _quit_webdriver_with_timeout(current_driver)
    if quit_error is not None:
        log_runtime_event(
            f"webdriver quit failed | error={quit_error!r} | browser_pid={browser_pid} | service_pid={service_pid} | user_data_dir={user_data_dir}"
        )
    elif not quit_finished:
        log_runtime_event(
            f"webdriver quit timed out | browser_pid={browser_pid} | service_pid={service_pid} | user_data_dir={user_data_dir}"
        )

    if not quit_finished or tracked_browser_pids or tracked_user_data_dirs:
        _cleanup_tracked_windows_browser_processes(tracked_browser_pids, tracked_user_data_dirs, service_pid)

    _clear_tracked_webdriver_processes()
    


atexit.register(_cleanup_webdriver_on_exit)