from __future__ import annotations

import json
import socket
import traceback
from dataclasses import dataclass
from urllib import error, request
from urllib.parse import urlparse

import app.extract.selenium as selenium_helpers
import app.extract.webdriver as webdriver_helpers
import cloudscraper

from app.settings.config import (
    APP_ROOT,
    DATA_ROOT,
    DATA_USER_ROOT,
    ENV_PATH,
    PROMPT_SETTINGS_PATH,
    get_runtime_settings,
    read_env_file,
)
from app.settings.default import DEFAULT_ENV_VALUES
from app.settings.logging import get_log_path, log_runtime_event
from app.settings.precheck import get_glossary_candidate_block_reason, get_translation_block_reason
from app.settings.setup import ensure_runtime_setup
from app.settings.update import UpdateNotConfiguredError, get_current_version, get_latest_release
from app.translation.engine import validate_glossary_file
from app.ui import render_diagnostics_screen
from app.utils.helpers import find_chapter_files, find_source_novels, parse_source_file


TARGET_LANG_LABELS = {
    "japanese": "일->한",
    "chinese": "중->한",
}
TARGET_LANG_ALIASES = {
    "ja": "japanese",
    "jp": "japanese",
    "japanese": "japanese",
    "zh": "chinese",
    "cn": "chinese",
    "ch": "chinese",
    "chinese": "chinese",
}


@dataclass(frozen=True)
class DiagnosticResult:
    name: str
    status: str
    detail: str


def _result(name: str, status: str, detail: str) -> DiagnosticResult:
    return DiagnosticResult(name=name, status=status, detail=detail)


def _format_result_line(result: DiagnosticResult) -> str:
    return f"[{result.status}] {result.name}: {result.detail}"


def _repair_status(existed_before: bool) -> str:
    return "PASS" if existed_before else "REPAIRED"


def _check_runtime_files() -> list[DiagnosticResult]:
    results: list[DiagnosticResult] = []
    expected_paths = {
        "env file": ENV_PATH,
        "prompt settings": PROMPT_SETTINGS_PATH,
        "data root": DATA_ROOT,
        "user data folder": DATA_USER_ROOT,
        "llama folder": DATA_ROOT / "llama",
        "models folder": DATA_ROOT / "models",
        "source folder": APP_ROOT / "source",
        "translated folder": APP_ROOT / "translated",
        "glossary folder": DATA_ROOT / "glossary",
        "default glossary": DATA_ROOT / "glossary" / "glossary.json",
    }
    existed_before = {name: path.exists() for name, path in expected_paths.items()}

    try:
        ensure_runtime_setup()
        repaired = [name for name, existed in existed_before.items() if not existed and expected_paths[name].exists()]
        if repaired:
            results.append(_result("runtime setup", "REPAIRED", "created missing defaults: " + ", ".join(repaired)))
        else:
            results.append(_result("runtime setup", "PASS", "default runtime files and folders are ready"))
    except Exception as exc:
        results.append(_result("runtime setup", "FAIL", str(exc)))
        return results

    env_values = read_env_file()
    if ENV_PATH.is_file():
        results.append(
            _result(
                "env file",
                _repair_status(existed_before["env file"]),
                f"loaded {len(env_values)} keys from {ENV_PATH}",
            )
        )
    else:
        results.append(_result("env file", "FAIL", f"missing file: {ENV_PATH}"))

    if PROMPT_SETTINGS_PATH.is_file():
        try:
            json.loads(PROMPT_SETTINGS_PATH.read_text(encoding="utf-8"))
            results.append(
                _result(
                    "prompt settings",
                    _repair_status(existed_before["prompt settings"]),
                    f"valid JSON: {PROMPT_SETTINGS_PATH}",
                )
            )
        except Exception as exc:
            results.append(_result("prompt settings", "FAIL", f"invalid JSON: {exc}"))
    else:
        results.append(_result("prompt settings", "FAIL", f"missing file: {PROMPT_SETTINGS_PATH}"))

    log_path = get_log_path()
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8"):
            pass
        results.append(_result("log file", "PASS", f"writable: {log_path}"))
    except Exception as exc:
        results.append(_result("log file", "FAIL", f"not writable: {exc}"))

    return results


def _check_runtime_paths() -> list[DiagnosticResult]:
    results: list[DiagnosticResult] = []

    try:
        settings = get_runtime_settings()
        results.append(_result("runtime settings", "PASS", "environment values parsed successfully"))
    except Exception as exc:
        results.append(_result("runtime settings", "FAIL", str(exc)))
        return results

    env_values = read_env_file()
    raw_target_lang = env_values.get("TARGET_LANG", DEFAULT_ENV_VALUES["TARGET_LANG"]).strip().lower()
    if raw_target_lang in TARGET_LANG_ALIASES:
        resolved_target_lang = TARGET_LANG_ALIASES[raw_target_lang]
        results.append(
            _result(
                "target language",
                "PASS",
                f"{resolved_target_lang} ({TARGET_LANG_LABELS.get(resolved_target_lang, resolved_target_lang)})",
            )
        )
    else:
        fallback_label = TARGET_LANG_LABELS.get(settings.target_lang, settings.target_lang)
        results.append(
            _result(
                "target language",
                "WARN",
                f"invalid TARGET_LANG={raw_target_lang!r}; using {settings.target_lang} ({fallback_label})",
            )
        )

    if settings.llama_server_path.is_file():
        results.append(_result("llama runtime", "PASS", f"server found: {settings.llama_server_path}"))
    else:
        results.append(_result("llama runtime", "WARN", f"server not found: {settings.llama_server_path}"))

    if settings.llama_model_path.is_file():
        results.append(_result("model file", "PASS", f"model found: {settings.llama_model_path.name}"))
    else:
        results.append(_result("model file", "FAIL", f"missing model: {settings.llama_model_path}"))

    if settings.glossary_path.is_file():
        glossary_warning = validate_glossary_file(settings.glossary_path)
        if glossary_warning is None:
            results.append(_result("glossary file", "PASS", f"valid JSON object: {settings.glossary_path}"))
        else:
            results.append(_result("glossary file", "FAIL", glossary_warning))
    else:
        results.append(_result("glossary file", "WARN", f"missing glossary: {settings.glossary_path}"))

    if settings.source_path.is_dir():
        results.append(_result("source folder", "PASS", f"source folder found: {settings.source_path}"))
    else:
        results.append(_result("source folder", "FAIL", f"missing source folder: {settings.source_path}"))

    try:
        settings.output_root.mkdir(parents=True, exist_ok=True)
        probe_path = settings.output_root / ".diagnostics_write_test.tmp"
        probe_path.write_text("ok", encoding="utf-8")
        probe_path.unlink(missing_ok=True)
        results.append(_result("output folder", "PASS", f"writable: {settings.output_root}"))
    except Exception as exc:
        results.append(_result("output folder", "FAIL", f"not writable: {exc}"))

    return results


def _check_source_inventory() -> list[DiagnosticResult]:
    results: list[DiagnosticResult] = []

    try:
        settings = get_runtime_settings()
    except Exception as exc:
        return [_result("source inventory", "FAIL", f"runtime settings unavailable: {exc}")]

    novel_dirs = find_source_novels(settings.source_path)
    chapter_count = sum(len(find_chapter_files(novel_dir)) for novel_dir in novel_dirs)
    if novel_dirs:
        results.append(
            _result(
                "source inventory",
                "PASS" if chapter_count > 0 else "WARN",
                f"{len(novel_dirs)} novel folders, {chapter_count} chapter files",
            )
        )
    else:
        results.append(_result("source inventory", "WARN", "no source novel folders found"))
        return results

    first_chapter = next((chapter for novel_dir in novel_dirs for chapter in find_chapter_files(novel_dir)), None)
    if first_chapter is None:
        results.append(_result("sample source parse", "WARN", "no chapter file available to validate"))
        return results

    try:
        document = parse_source_file(first_chapter)
        results.append(
            _result(
                "sample source parse",
                "PASS",
                f"{first_chapter.name} parsed, title='{document.title[:30]}' body_chars={len(document.body)}",
            )
        )
    except Exception as exc:
        results.append(_result("sample source parse", "FAIL", f"{first_chapter}: {exc}"))

    return results


def _check_feature_prerequisites() -> list[DiagnosticResult]:
    results: list[DiagnosticResult] = []

    translation_reason = get_translation_block_reason()
    if translation_reason is None:
        results.append(_result("translation precheck", "PASS", "ready to run translation"))
    else:
        results.append(_result("translation precheck", "FAIL", translation_reason))

    glossary_reason = get_glossary_candidate_block_reason()
    if glossary_reason is None:
        results.append(_result("glossary precheck", "PASS", "ready to build glossary candidates"))
    else:
        results.append(_result("glossary precheck", "FAIL", glossary_reason))

    return results


def _check_server_health() -> list[DiagnosticResult]:
    results: list[DiagnosticResult] = []

    try:
        settings = get_runtime_settings()
    except Exception as exc:
        return [_result("server health", "FAIL", f"runtime settings unavailable: {exc}")]

    server_url = settings.server_url.rstrip("/")
    health_url = server_url + "/health"
    parsed = urlparse(server_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)

    if not settings.llama_server_path.is_file():
        results.append(_result("server health", "WARN", f"server binary missing: {settings.llama_server_path}"))
        return results

    try:
        with socket.create_connection((host, port), timeout=1.5):
            pass
    except OSError:
        results.append(_result("server health", "PASS", f"server binary ready, service currently stopped: {server_url}"))
        return results

    try:
        req = request.Request(health_url, method="GET")
        with request.urlopen(req, timeout=3) as response:
            results.append(_result("server health", "PASS", f"{health_url} responded with {response.status}"))
    except error.URLError as exc:
        results.append(_result("server health", "WARN", f"port is open but health check failed: {exc.reason}"))
    except Exception as exc:
        results.append(_result("server health", "WARN", f"health check failed: {exc}"))

    return results


def _check_crawler_stack() -> list[DiagnosticResult]:
    results: list[DiagnosticResult] = []

    try:
        scraper = cloudscraper.create_scraper(
            browser={
                "browser": "chrome",
                "platform": "windows",
                "desktop": True,
            }
        )
        user_agent = scraper.headers.get("User-Agent", "")
        results.append(_result("cloudscraper", "PASS", f"session created, user-agent length={len(user_agent)}"))
    except Exception as exc:
        results.append(_result("cloudscraper", "FAIL", str(exc)))

    try:
        selenium_helpers._import_external_selenium_module("selenium.webdriver.support.ui")
        webdriver_helpers._import_external_selenium_module("selenium.webdriver.edge.options")
        results.append(_result("selenium imports", "PASS", "webdriver support modules imported successfully"))

        browser_name = webdriver_helpers._choose_browser("auto")
        browser_path = webdriver_helpers._windows_browser_path(browser_name)
        browser_detail = browser_name if browser_path is None else f"{browser_name} at {browser_path}"
        results.append(_result("browser detection", "PASS", browser_detail))
    except Exception as exc:
        results.append(_result("selenium imports", "FAIL", str(exc)))

    try:
        import seleniumbase  # noqa: F401

        results.append(_result("seleniumbase", "PASS", "package import succeeded"))
    except Exception as exc:
        results.append(_result("seleniumbase", "WARN", str(exc)))

    return results


def _check_update_release() -> list[DiagnosticResult]:
    try:
        release = get_latest_release()
    except UpdateNotConfiguredError as exc:
        return [_result("update release", "WARN", f"repository not configured: {exc}")]
    except Exception as exc:
        return [_result("update release", "WARN", f"latest release check failed: {exc}")]

    return [
        _result("current version", "PASS", get_current_version()),
        _result("update release", "PASS", f"{release.tag_name} ({release.html_url})"),
        _result("update asset", "PASS", f"{release.asset.name}"),
    ]


def run_full_diagnostics() -> str:
    log_runtime_event("diagnostics start")

    sections: list[tuple[str, list[DiagnosticResult]]] = []
    try:
        sections.append(("Runtime", _check_runtime_files()))
        sections.append(("Paths", _check_runtime_paths()))
        sections.append(("Source", _check_source_inventory()))
        sections.append(("Features", _check_feature_prerequisites()))
        sections.append(("Server", _check_server_health()))
        sections.append(("Update", _check_update_release()))
        sections.append(("Crawler", _check_crawler_stack()))
    except Exception as exc:
        fallback_lines = [
            "[FAIL] diagnostics: unexpected error",
            str(exc),
            traceback.format_exc().strip(),
        ]
        render_diagnostics_screen(
            lines=fallback_lines,
            summary="FAIL 1 | WARN 0 | PASS 0",
            status_message="Unexpected diagnostics error",
        )
        log_runtime_event(f"diagnostics crashed | error={exc!r}\n{traceback.format_exc()}")
        return "[ERROR] Diagnostics failed unexpectedly."

    lines: list[str] = []
    total_pass = 0
    total_repaired = 0
    total_warn = 0
    total_fail = 0

    for title, results in sections:
        lines.append(f"== {title} ==")
        for result in results:
            lines.append(_format_result_line(result))
            if result.status == "PASS":
                total_pass += 1
            elif result.status == "REPAIRED":
                total_repaired += 1
            elif result.status == "WARN":
                total_warn += 1
            elif result.status == "FAIL":
                total_fail += 1
        lines.append("")

    if lines and lines[-1] == "":
        lines.pop()

    summary = f"FAIL {total_fail} | WARN {total_warn} | REPAIRED {total_repaired} | PASS {total_pass}"
    render_diagnostics_screen(
        lines=lines,
        summary=summary,
        status_message="Press Enter to return to the main menu.",
    )
    log_runtime_event(f"diagnostics complete | fail={total_fail} | warn={total_warn} | pass={total_pass}")

    if total_fail > 0:
        return f"[WARN] Diagnostics complete: {summary}"
    if total_warn > 0:
        return f"[INFO] Diagnostics complete with warnings: {summary}"
    return f"[INFO] Diagnostics complete: {summary}"
