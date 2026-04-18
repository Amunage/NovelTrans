from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from zipfile import ZipFile

from app.config import (
    APP_ROOT,
    DEFAULT_ENV_CONTENT,
    DEFAULT_GLOSSARY_CONTENT,
    get_configured_model_path,
    update_env_value,
)
from app.ui import prompt_for_model_download


GITHUB_RELEASES_API_URL = "https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=5"
GITHUB_API_HEADERS = {
    "Accept": "application/vnd.github+json",
    "User-Agent": "noveltrans-runtime-setup",
}
CUDA_13_WINDOWS_BINARY_PATTERN = re.compile(r"^llama-.*-bin-win-cuda-13\.1-x64\.zip$")
CUDA_13_WINDOWS_ASSET = "cudart-llama-bin-win-cuda-13.1-x64.zip"
CUDA_12_WINDOWS_BINARY_PATTERN = re.compile(r"^llama-.*-bin-win-cuda-12\.4-x64\.zip$")
CUDA_12_WINDOWS_ASSET = "cudart-llama-bin-win-cuda-12.4-x64.zip"
CPU_WINDOWS_ASSET_PATTERN = re.compile(r"^llama-.*-bin-win-cpu-x64\.zip$")
LLAMA_RUNTIME_METADATA = ".noveltrans-runtime.json"
HUGGING_FACE_MODEL_REPO = "unsloth/gemma-4-26B-A4B-it-GGUF"
HUGGING_FACE_API_URL = f"https://huggingface.co/api/models/{HUGGING_FACE_MODEL_REPO}?expand[]=siblings"
HUGGING_FACE_HEADERS = {
    "User-Agent": "noveltrans-runtime-setup",
}
MODEL_SETUP_METADATA = ".noveltrans-model.json"
MODEL_CANDIDATES = [
    {
        "filename": "gemma-4-26B-A4B-it-Q8_0.gguf",
        "label": "Q8_0",
        "summary": "최고 품질, 다만 매우 무겁습니다.",
        "min_vram_gb": 28.0,
        "min_ram_gb": 64.0,
    },
    {
        "filename": "gemma-4-26B-A4B-it-UD-Q5_K_M.gguf",
        "label": "Q5_K_M",
        "summary": "고품질 우선, 24GB 이상 GPU에 적합합니다.",
        "min_vram_gb": 24.0,
        "min_ram_gb": 48.0,
    },
    {
        "filename": "gemma-4-26B-A4B-it-UD-Q4_K_M.gguf",
        "label": "Q4_K_M",
        "summary": "균형형이며 16GB VRAM 환경에서는 다소 빡빡할 수 있습니다.",
        "min_vram_gb": 18.0,
        "min_ram_gb": 32.0,
    },
    {
        "filename": "gemma-4-26B-A4B-it-UD-IQ4_XS.gguf",
        "label": "IQ4_XS",
        "summary": "16GB 전후 GPU에서 가장 무난한 권장값입니다.",
        "min_vram_gb": 14.0,
        "min_ram_gb": 24.0,
    },
    {
        "filename": "gemma-4-26B-A4B-it-UD-Q2_K_XL.gguf",
        "label": "Q2_K_XL",
        "summary": "메모리가 부족할 때를 위한 최소 옵션입니다.",
        "min_vram_gb": 10.0,
        "min_ram_gb": 16.0,
    },
]


def ensure_runtime_setup() -> None:
    ensure_default_project_files()
    ensure_llama_cpp_runtime(APP_ROOT)


def ensure_default_project_files() -> None:
    env_path = APP_ROOT / ".env"
    if not env_path.exists():
        env_path.write_text(DEFAULT_ENV_CONTENT, encoding="utf-8")

    glossary_dir = APP_ROOT / "glossary"
    glossary_dir.mkdir(parents=True, exist_ok=True)

    glossary_path = glossary_dir / "glossary.json"
    if not glossary_path.exists():
        glossary_path.write_text(DEFAULT_GLOSSARY_CONTENT, encoding="utf-8")


def run_model_download_setup(force_prompt: bool = True) -> str:
    env_path = APP_ROOT / ".env"
    return ensure_gemma_model_runtime(APP_ROOT, env_path, force_prompt=force_prompt)


def ensure_llama_cpp_runtime(app_root: Path) -> None:
    if _is_truthy(os.environ.get("NOVELTRANS_SKIP_LLAMA_SETUP")):
        return

    runtime_dir = app_root / "llama"
    server_path = runtime_dir / "llama-server.exe"
    if server_path.is_file():
        return

    if platform.system() != "Windows":
        return

    if platform.machine().lower() not in {"amd64", "x86_64"}:
        return

    try:
        cuda_version = detect_cuda_version()
        release, assets = resolve_llama_cpp_assets(cuda_version)
        install_llama_cpp_assets(assets, runtime_dir)
        write_runtime_metadata(runtime_dir, release["tag_name"], assets, cuda_version)
        installed_assets = ", ".join(str(asset["name"]) for asset in assets)
        print(f"[INFO] llama.cpp runtime installed: {installed_assets}")
    except Exception as exc:
        print(f"[WARN] llama.cpp runtime auto install skipped: {exc}")


def ensure_gemma_model_runtime(app_root: Path, env_path: Path, *, force_prompt: bool = False) -> str:
    if _is_truthy(os.environ.get("NOVELTRANS_SKIP_MODEL_SETUP")):
        return "[INFO] 모델 자동 다운로드가 비활성화되어 있습니다."

    configured_model_path = get_configured_model_path(env_path)
    if configured_model_path.is_file() and not force_prompt:
        return f"[INFO] 현재 모델 사용 중: {configured_model_path.name}"

    if not _is_interactive_terminal():
        return "[WARN] Gemma 모델 다운로드 메뉴를 열 수 없는 환경입니다."

    models_dir = app_root / "models"
    models_dir.mkdir(parents=True, exist_ok=True)

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

    return install_selected_model_option(app_root, env_path, models_dir, selected_option, system_specs)


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
    output = _run_command(command)
    if output is None:
        return 0.0

    match = re.search(r"(\d+(?:\.\d+)?)", output)
    return float(match.group(1)) if match else 0.0


def detect_gpu_specs() -> tuple[str | None, float | None]:
    output = _run_command(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"])
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
    available_filenames = fetch_huggingface_filenames()
    options: list[dict[str, object]] = []

    for candidate in MODEL_CANDIDATES:
        filename = str(candidate["filename"])
        if filename not in available_filenames:
            continue

        option = dict(candidate)
        option["download_url"] = build_huggingface_download_url(filename)
        option["size_bytes"] = fetch_remote_file_size(str(option["download_url"]))
        options.append(option)

    return options


def fetch_huggingface_filenames() -> set[str]:
    request = urllib.request.Request(HUGGING_FACE_API_URL, headers=HUGGING_FACE_HEADERS)
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.load(response)

    siblings = payload.get("siblings", [])
    if not isinstance(siblings, list):
        return set()

    filenames: set[str] = set()
    for sibling in siblings:
        if isinstance(sibling, dict):
            rfilename = sibling.get("rfilename")
            if isinstance(rfilename, str):
                filenames.add(rfilename)
    return filenames


def build_huggingface_download_url(filename: str) -> str:
    encoded_filename = urllib.parse.quote(filename, safe="/")
    return f"https://huggingface.co/{HUGGING_FACE_MODEL_REPO}/resolve/main/{encoded_filename}"


def fetch_remote_file_size(download_url: str) -> int | None:
    request = urllib.request.Request(download_url, headers=HUGGING_FACE_HEADERS, method="HEAD")
    with urllib.request.urlopen(request, timeout=30) as response:
        return _get_content_length(response)


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
    if not model_destination.is_file():
        download_file(
            build_huggingface_download_url(str(selected_option["filename"])),
            model_destination,
            str(selected_option["filename"]),
            1,
            1,
            request_headers=HUGGING_FACE_HEADERS,
        )

    update_env_value("LLAMA_MODEL_PATH", to_relative_env_path(model_destination, app_root), env_path)
    write_model_metadata(models_dir, selected_option, system_specs)
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
        "repo": HUGGING_FACE_MODEL_REPO,
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


def detect_cuda_version() -> tuple[int, int] | None:
    nvcc_output = _run_command(["nvcc", "--version"])
    if nvcc_output:
        match = re.search(r"release\s+(\d+)\.(\d+)", nvcc_output)
        if match:
            return int(match.group(1)), int(match.group(2))

    nvidia_smi_output = _run_command(["nvidia-smi"])
    if nvidia_smi_output:
        match = re.search(r"CUDA Version:\s+(\d+)\.(\d+)", nvidia_smi_output)
        if match:
            return int(match.group(1)), int(match.group(2))

    return None


def resolve_llama_cpp_assets(
    cuda_version: tuple[int, int] | None,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    preferred_asset_groups = get_preferred_asset_groups(cuda_version)
    releases = fetch_llama_cpp_releases()

    for release in releases:
        assets = find_matching_asset_group(release.get("assets", []), preferred_asset_groups)
        if assets is not None:
            return release, assets

    preferred = ", ".join(" + ".join(group) for group in preferred_asset_groups)
    raise RuntimeError(f"Could not find a matching llama.cpp asset set for: {preferred}")


def get_preferred_asset_groups(cuda_version: tuple[int, int] | None) -> list[list[str]]:
    if cuda_version is not None:
        if cuda_version >= (13, 0):
            return [
                ["cuda13_binary", CUDA_13_WINDOWS_ASSET],
                ["cuda12_binary", CUDA_12_WINDOWS_ASSET],
                ["cpu"],
            ]
        if cuda_version >= (12, 0):
            return [
                ["cuda12_binary", CUDA_12_WINDOWS_ASSET],
                ["cuda13_binary", CUDA_13_WINDOWS_ASSET],
                ["cpu"],
            ]
    return [["cpu"]]


def fetch_llama_cpp_releases() -> list[dict[str, object]]:
    request = urllib.request.Request(GITHUB_RELEASES_API_URL, headers=GITHUB_API_HEADERS)
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.load(response)

    if not isinstance(payload, list) or not payload:
        raise RuntimeError("GitHub releases API returned no releases")

    return [item for item in payload if isinstance(item, dict)]


def find_matching_asset_group(
    assets: object,
    preferred_asset_groups: list[list[str]],
) -> list[dict[str, object]] | None:
    if not isinstance(assets, list):
        return None

    normalized_assets = [asset for asset in assets if isinstance(asset, dict)]

    for asset_group in preferred_asset_groups:
        matched_assets: list[dict[str, object]] = []
        for preferred in asset_group:
            asset = find_asset_by_selector(normalized_assets, preferred)
            if asset is None:
                matched_assets = []
                break
            matched_assets.append(asset)
        if matched_assets:
            return matched_assets

    return None


def find_asset_by_selector(assets: list[dict[str, object]], selector: str) -> dict[str, object] | None:
    for asset in assets:
        asset_name = asset.get("name")
        if not isinstance(asset_name, str):
            continue

        if selector == "cpu" and CPU_WINDOWS_ASSET_PATTERN.match(asset_name):
            return asset
        if selector == "cuda13_binary" and CUDA_13_WINDOWS_BINARY_PATTERN.match(asset_name):
            return asset
        if selector == "cuda12_binary" and CUDA_12_WINDOWS_BINARY_PATTERN.match(asset_name):
            return asset
        if asset_name == selector:
            return asset

    return None


def install_llama_cpp_assets(assets: list[dict[str, object]], runtime_dir: Path) -> None:
    runtime_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="noveltrans-llama-setup-") as temp_dir_str:
        temp_dir = Path(temp_dir_str)
        for index, asset in enumerate(assets, start=1):
            asset_name = str(asset["name"])
            archive_path = temp_dir / asset_name
            extract_dir = temp_dir / f"extract-{index}"
            extract_dir.mkdir(parents=True, exist_ok=True)

            download_file(
                str(asset["browser_download_url"]),
                archive_path,
                asset_name,
                index,
                len(assets),
                request_headers=GITHUB_API_HEADERS,
            )

            with ZipFile(archive_path) as archive:
                archive.extractall(extract_dir)

            source_root = get_single_root_or_self(extract_dir)
            for child in source_root.iterdir():
                destination = runtime_dir / child.name
                if child.is_dir():
                    shutil.copytree(child, destination, dirs_exist_ok=True)
                else:
                    shutil.copy2(child, destination)

    server_path = runtime_dir / "llama-server.exe"
    if not server_path.is_file():
        raise RuntimeError("Downloaded llama.cpp asset did not contain llama-server.exe")


def download_file(
    download_url: str,
    destination: Path,
    asset_name: str,
    asset_index: int,
    total_assets: int,
    request_headers: dict[str, str] | None = None,
) -> None:
    request = urllib.request.Request(download_url, headers=request_headers or GITHUB_API_HEADERS)
    try:
        os.system("cls")
        with urllib.request.urlopen(request, timeout=120) as response, destination.open("wb") as output:
            total_size = _get_content_length(response)
            downloaded = 0
            next_report_percent = 0
            last_unknown_report_at = 0.0
            print(f"[INFO] Downloading llama.cpp asset {asset_index}/{total_assets}: {asset_name}")

            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break

                output.write(chunk)
                downloaded += len(chunk)
                next_report_percent, last_unknown_report_at = _report_download_progress(
                    asset_name,
                    downloaded,
                    total_size,
                    next_report_percent,
                    last_unknown_report_at,
                )

            if total_size is None:
                _finish_progress_line(f"[INFO] Download complete: {asset_name} ({_format_size(downloaded)})")
            elif next_report_percent <= 100:
                _finish_progress_line(
                    f"[INFO] Download complete: {asset_name} (100%, {_format_size(downloaded)})"
                )
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Failed to download {download_url}") from exc


def get_single_root_or_self(path: Path) -> Path:
    children = list(path.iterdir())
    if len(children) == 1 and children[0].is_dir():
        return children[0]
    return path


def write_runtime_metadata(
    runtime_dir: Path,
    release_tag: object,
    assets: list[dict[str, object]],
    cuda_version: tuple[int, int] | None,
) -> None:
    metadata = {
        "release_tag": release_tag,
        "asset_names": [asset.get("name") for asset in assets],
        "cuda_version": f"{cuda_version[0]}.{cuda_version[1]}" if cuda_version else None,
    }
    metadata_path = runtime_dir / LLAMA_RUNTIME_METADATA
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")


def _run_command(command: list[str]) -> str | None:
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=10,
        )
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return None

    stdout = completed.stdout.strip()
    stderr = completed.stderr.strip()
    combined = "\n".join(part for part in (stdout, stderr) if part)
    return combined or None


def _is_truthy(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _is_interactive_terminal() -> bool:
    return bool(getattr(sys.stdin, "isatty", lambda: False)() and getattr(sys.stdout, "isatty", lambda: False)())


def _get_content_length(response: object) -> int | None:
    headers = getattr(response, "headers", None)
    if headers is None:
        return None

    content_length = headers.get("Content-Length")
    if not content_length:
        return None

    try:
        return int(content_length)
    except ValueError:
        return None


def _report_download_progress(
    asset_name: str,
    downloaded: int,
    total_size: int | None,
    next_report_percent: int,
    last_unknown_report_at: float,
) -> tuple[int, float]:
    if total_size is None or total_size <= 0:
        now = time.monotonic()
        if now - last_unknown_report_at >= 0.25:
            _render_progress_line(f"[INFO] {asset_name}: {_format_size(downloaded)} downloaded...")
            last_unknown_report_at = now
        return next_report_percent, last_unknown_report_at

    percent = int(downloaded * 100 / total_size)
    while percent >= next_report_percent and next_report_percent <= 100:
        _render_progress_line(
            f"[INFO] {asset_name}: {next_report_percent}% "
            f"({_format_size(downloaded)} / {_format_size(total_size)})"
        )
        next_report_percent += 10

    return next_report_percent, last_unknown_report_at


def _format_size(size_in_bytes: int) -> str:
    return f"{size_in_bytes / (1024 * 1024):.1f} MB"


def _render_progress_line(message: str) -> None:
    width = shutil.get_terminal_size(fallback=(100, 20)).columns
    padded_message = message.ljust(max(width - 1, len(message)))
    sys.stdout.write(f"\r{padded_message}")
    sys.stdout.flush()


def _finish_progress_line(message: str) -> None:
    _render_progress_line(message)
    sys.stdout.write("\n")
    sys.stdout.flush()
