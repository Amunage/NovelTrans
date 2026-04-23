from __future__ import annotations

import json
import os
import platform
import re
import shutil
import sys
from pathlib import Path

from app.settings.config import (
    APP_ROOT,
    DATA_USER_ROOT,
    DATA_ROOT,
    ENV_PATH,
    PROMPT_SETTINGS_PATH,
    get_configured_model_path,
    update_env_value,
)
from app.settings.default import (
    DEFAULT_ENV_CONTENT,
    DEFAULT_ENV_VALUES,
    DEFAULT_GLOSSARY_CONTENT,
    DEFAULT_PROMPT_CONTENT,
)
from app.settings.downloads import DownloadCancelledError, download_file, fetch_remote_file_size
from app.settings.logging import log_runtime_event
from app.settings.setmodel import (
    HUGGING_FACE_HEADERS,
    MODEL_CANDIDATES,
    build_huggingface_download_url,
    detect_cuda_version,
    fetch_huggingface_filenames,
    install_llama_cpp_assets,
    resolve_llama_cpp_assets,
    run_command,
    write_runtime_metadata,
)
from app.ui.settings_flow import prompt_for_model_download, prompt_llama_runtime_install
from app.ui import render_download_progress_screen


MODEL_SETUP_METADATA = ".noveltrans-model.json"


def ensure_runtime_setup() -> None:
    log_runtime_event(f"ensure_runtime_setup start | app_root={APP_ROOT} | data_root={DATA_ROOT}")
    ensure_default_project_files()
    log_runtime_event("ensure_runtime_setup done")


def ensure_default_project_files() -> None:
    DATA_USER_ROOT.mkdir(parents=True, exist_ok=True)

    env_path = ENV_PATH
    if not env_path.exists():
        env_path.write_text(DEFAULT_ENV_CONTENT, encoding="utf-8")
        log_runtime_event(f"created env file | path={env_path}")
    else:
        env_content = env_path.read_text(encoding="utf-8")
        if "TARGET_LANG=" not in env_content:
            update_env_value("TARGET_LANG", DEFAULT_ENV_VALUES["TARGET_LANG"], env_path)
            log_runtime_event(f"added missing env default | key=TARGET_LANG | path={env_path}")

    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    if not PROMPT_SETTINGS_PATH.exists():
        PROMPT_SETTINGS_PATH.write_text(DEFAULT_PROMPT_CONTENT, encoding="utf-8")
        log_runtime_event(f"created prompt settings file | path={PROMPT_SETTINGS_PATH}")

    (DATA_ROOT / "llama").mkdir(parents=True, exist_ok=True)
    (DATA_ROOT / "models").mkdir(parents=True, exist_ok=True)
    log_runtime_event(f"ensured runtime dirs | llama={DATA_ROOT / 'llama'} | models={DATA_ROOT / 'models'}")

    glossary_dir = DATA_ROOT / "glossary"
    glossary_dir.mkdir(parents=True, exist_ok=True)

    glossary_path = glossary_dir / "glossary.json"
    if not glossary_path.exists():
        glossary_path.write_text(DEFAULT_GLOSSARY_CONTENT, encoding="utf-8")
        log_runtime_event(f"created glossary file | path={glossary_path}")


def run_model_download_setup(force_prompt: bool = True) -> str:
    env_path = ENV_PATH
    return ensure_gemma_model_runtime(DATA_ROOT, env_path, force_prompt=force_prompt)


def ensure_llama_cpp_runtime(app_root: Path, *, confirm_install: bool = False) -> str | None:
    if _is_truthy(os.environ.get("NOVELTRANS_SKIP_LLAMA_SETUP")):
        return "[INFO] llama.cpp 런타임 자동 설치가 비활성화되어 있습니다."

    runtime_dir = app_root / "llama"
    server_path = runtime_dir / "llama-server.exe"
    log_runtime_event(f"ensure_llama_cpp_runtime check | runtime_dir={runtime_dir} | server_path={server_path}")
    if server_path.is_file():
        log_runtime_event("ensure_llama_cpp_runtime skipped | existing server found")
        return None

    if confirm_install and not prompt_llama_runtime_install(server_path):
        log_runtime_event(f"llama runtime install declined | server_path={server_path}")
        return "[INFO] llama.cpp 런타임 설치를 취소했습니다."

    if platform.system() != "Windows":
        return "[WARN] Windows 환경이 아니어서 llama.cpp 런타임 자동 설치를 건너뜁니다."

    if platform.machine().lower() not in {"amd64", "x86_64"}:
        return "[WARN] 지원하지 않는 CPU 아키텍처라 llama.cpp 런타임 자동 설치를 건너뜁니다."

    try:
        cuda_version = detect_cuda_version(run_command)
        release, assets = resolve_llama_cpp_assets(cuda_version)
        install_llama_cpp_assets(assets, runtime_dir, render_progress=_render_llama_runtime_download_progress)
        write_runtime_metadata(runtime_dir, release["tag_name"], assets, cuda_version)
        installed_assets = ", ".join(str(asset["name"]) for asset in assets)
        print(f"[INFO] llama.cpp runtime installed: {installed_assets} -> {runtime_dir}")
        log_runtime_event(f"llama runtime installed | assets={installed_assets} | runtime_dir={runtime_dir}")
        return f"[INFO] llama.cpp 런타임 설치 완료: {runtime_dir}"
    except DownloadCancelledError as exc:
        print(f"[INFO] llama.cpp runtime download cancelled: {exc.asset_name}")
        log_runtime_event(f"llama runtime download cancelled | asset={exc.asset_name}")
        return f"[INFO] llama.cpp 런타임 다운로드를 취소했습니다: {exc.asset_name}"
    except Exception as exc:
        print(f"[WARN] llama.cpp runtime auto install skipped: {exc}")
        log_runtime_event(f"llama runtime auto install skipped | error={exc!r}")
        return f"[WARN] llama.cpp 런타임 자동 설치를 건너뜁니다: {exc}"


def ensure_gemma_model_runtime(app_root: Path, env_path: Path, *, force_prompt: bool = False) -> str:
    if _is_truthy(os.environ.get("NOVELTRANS_SKIP_MODEL_SETUP")):
        return "[INFO] 모델 자동 다운로드가 비활성화되어 있습니다."

    configured_model_path = get_configured_model_path(env_path)
    log_runtime_event(
        f"ensure_gemma_model_runtime start | app_root={app_root} | env_path={env_path} | "
        f"configured_model_path={configured_model_path} | force_prompt={force_prompt}"
    )
    if configured_model_path.is_file() and not force_prompt:
        return f"[INFO] 현재 모델 사용 중: {configured_model_path.name}"

    if not _is_interactive_terminal():
        return "[WARN] Gemma 모델 다운로드 메뉴를 열 수 없는 환경입니다."

    models_dir = app_root / "models"
    models_dir.mkdir(parents=True, exist_ok=True)
    log_runtime_event(f"ensure_gemma_model_runtime models_dir={models_dir}")

    try:
        system_specs = detect_system_specs(models_dir)
        model_options = fetch_model_options()
    except Exception as exc:
        return f"[WARN] Gemma 모델 추천 정보를 불러오지 못했습니다. {exc}"

    if not model_options:
        return "[WARN] 다운로드 가능한 Gemma 모델 목록을 찾지 못했습니다."

    recommended_index = choose_recommended_model_index(system_specs, model_options)
    selected_option = prompt_for_model_download(system_specs, model_options, recommended_index)
    if selected_option is None:
        return "[INFO] Gemma 모델 다운로드를 취소했습니다."

    try:
        return install_selected_model_option(app_root, env_path, models_dir, selected_option, system_specs)
    except DownloadCancelledError as exc:
        return f"[INFO] 다운로드를 취소했습니다: {exc.asset_name}"


def detect_system_specs(models_dir: Path) -> dict[str, object]:
    ram_gb = detect_system_ram_gb()
    gpu_name, gpu_vram_gb = detect_gpu_specs()
    disk_free_gb = shutil.disk_usage(models_dir).free / (1024**3)
    return {
        "ram_gb": ram_gb,
        "gpu_name": gpu_name,
        "gpu_vram_gb": gpu_vram_gb,
        "disk_free_gb": disk_free_gb,
        "cpu_threads": os.cpu_count() or 0,
    }


def detect_system_ram_gb() -> float:
    if platform.system() != "Windows":
        return 0.0

    command = [
        "powershell",
        "-NoProfile",
        "-Command",
        "[math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB, 2)",
    ]
    output = run_command(command)
    if output is None:
        return 0.0

    match = re.search(r"(\d+(?:\.\d+)?)", output)
    return float(match.group(1)) if match else 0.0


def detect_gpu_specs() -> tuple[str | None, float | None]:
    output = run_command(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"])
    if output:
        first_line = output.splitlines()[0]
        parts = [part.strip() for part in first_line.split(",")]
        if len(parts) >= 2:
            try:
                return parts[0], float(parts[1]) / 1024
            except ValueError:
                return parts[0], None
        if parts:
            return parts[0], None

    return None, None


def fetch_model_options() -> list[dict[str, object]]:
    available_filenames_by_repo = fetch_huggingface_filenames()
    options: list[dict[str, object]] = []

    for candidate in MODEL_CANDIDATES:
        repo = str(candidate["repo"])
        filename = str(candidate["filename"])
        if filename not in available_filenames_by_repo.get(repo, set()):
            continue

        option = dict(candidate)
        option["download_url"] = build_huggingface_download_url(repo, filename)
        option["size_bytes"] = fetch_remote_file_size(str(option["download_url"]), request_headers=HUGGING_FACE_HEADERS)
        options.append(option)

    return options


def choose_recommended_model_index(
    system_specs: dict[str, object],
    model_options: list[dict[str, object]],
) -> int:
    gpu_vram_gb = system_specs.get("gpu_vram_gb")
    ram_gb = float(system_specs.get("ram_gb", 0.0))
    disk_free_gb = float(system_specs.get("disk_free_gb", 0.0))

    for index, option in enumerate(model_options):
        size_gb = get_size_gb(option.get("size_bytes"))
        if gpu_vram_gb is not None and float(gpu_vram_gb) < float(option["min_vram_gb"]):
            continue
        if ram_gb < float(option["min_ram_gb"]):
            continue
        if size_gb is not None and disk_free_gb < size_gb + 2.0:
            continue
        return index

    return len(model_options) - 1


def install_selected_model_option(
    app_root: Path,
    env_path: Path,
    models_dir: Path,
    selected_option: dict[str, object],
    system_specs: dict[str, object],
) -> str:
    model_destination = models_dir / str(selected_option["filename"])
    log_runtime_event(
        f"install_selected_model_option start | model={selected_option['filename']} | "
        f"model_destination={model_destination} | models_dir={models_dir}"
    )
    if not model_destination.is_file():
        download_file(
            build_huggingface_download_url(str(selected_option["repo"]), str(selected_option["filename"])),
            model_destination,
            str(selected_option["filename"]),
            1,
            1,
            request_headers=HUGGING_FACE_HEADERS,
            render_progress=lambda asset_name, percent, speed_mbps: _render_model_download_progress(
                asset_name,
                model_destination,
                percent,
                speed_mbps,
            ),
        )

    write_model_metadata(models_dir, selected_option, system_specs)
    update_env_value("LLAMA_MODEL_PATH", to_relative_env_path(model_destination, app_root), env_path)
    log_runtime_event(
        f"install_selected_model_option complete | model={selected_option['filename']} | "
        f"saved_to={model_destination} | env_path={env_path}"
    )
    return f"[INFO] Gemma 모델 준비 완료: {selected_option['filename']}"


def get_size_gb(size_bytes: object) -> float | None:
    if not isinstance(size_bytes, int):
        return None
    return size_bytes / (1024**3)


def write_model_metadata(
    models_dir: Path,
    selected_option: dict[str, object],
    system_specs: dict[str, object],
) -> None:
    metadata = {
        "repo": selected_option["repo"],
        "selected_model": selected_option["filename"],
        "selected_label": selected_option["label"],
        "size_bytes": selected_option.get("size_bytes"),
        "system_specs": system_specs,
    }
    metadata_path = models_dir / MODEL_SETUP_METADATA
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")


def to_relative_env_path(path: Path, app_root: Path) -> str:
    try:
        return path.relative_to(app_root).as_posix()
    except ValueError:
        return str(path)


def _is_truthy(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _is_interactive_terminal() -> bool:
    return bool(getattr(sys.stdin, "isatty", lambda: False)() and getattr(sys.stdout, "isatty", lambda: False)())


def _render_model_download_progress(
    asset_name: str,
    destination_path: Path,
    percent: int,
    speed_mbps: float | None,
) -> None:
    render_download_progress_screen(
        title="Gemma 4 모델 자동 다운로드",
        message="모델을 다운로드 중입니다.",
        item_label="모델",
        item_name=asset_name,
        destination_path=str(destination_path),
        percent=max(0, min(percent, 100)),
        speed_mbps=speed_mbps,
    )


def _render_llama_runtime_download_progress(
    asset_name: str,
    destination_path: Path,
    percent: int,
    speed_mbps: float | None,
) -> None:
    render_download_progress_screen(
        title="llama.cpp 런타임 자동 다운로드",
        message="런타임을 다운로드 중입니다.",
        item_label="파일",
        item_name=asset_name,
        destination_path=str(destination_path),
        percent=max(0, min(percent, 100)),
        speed_mbps=speed_mbps,
    )
