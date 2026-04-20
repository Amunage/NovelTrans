from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable
from zipfile import ZipFile

from app.config import log_runtime_event
from app.downloads import download_file


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
HUGGING_FACE_MODEL_REPOS = {
    "gemma-4-E4B-it-GGUF": "unsloth/gemma-4-E4B-it-GGUF",
    "gemma-4-26B-A4B-it-GGUF": "unsloth/gemma-4-26B-A4B-it-GGUF",
}
HUGGING_FACE_HEADERS = {
    "User-Agent": "noveltrans-runtime-setup",
}
MODEL_CANDIDATES = [
    {
        "repo": HUGGING_FACE_MODEL_REPOS["gemma-4-26B-A4B-it-GGUF"],
        "filename": "gemma-4-26B-A4B-it-UD-Q5_K_M.gguf",
        "label": "26B Q5_K_M",
        "summary": "고품질 우선 설정입니다.",
        "min_vram_gb": 24.0,
        "min_ram_gb": 48.0,
    },
    {
        "repo": HUGGING_FACE_MODEL_REPOS["gemma-4-26B-A4B-it-GGUF"],
        "filename": "gemma-4-26B-A4B-it-UD-Q4_K_M.gguf",
        "label": "26B Q4_K_M",
        "summary": "26B 계열의 균형형 설정입니다.",
        "min_vram_gb": 18.0,
        "min_ram_gb": 32.0,
    },
    {
        "repo": HUGGING_FACE_MODEL_REPOS["gemma-4-26B-A4B-it-GGUF"],
        "filename": "gemma-4-26B-A4B-it-UD-IQ4_XS.gguf",
        "label": "26B IQ4_XS",
        "summary": "26B 계열에서 가장 무난한 권장값입니다.",
        "min_vram_gb": 14.0,
        "min_ram_gb": 24.0,
    },
    {
        "repo": HUGGING_FACE_MODEL_REPOS["gemma-4-E4B-it-GGUF"],
        "filename": "gemma-4-E4B-it-Q6_K.gguf",
        "label": "E4B Q6_K",
        "summary": "E4B 계열 상급 품질 설정입니다.",
        "min_vram_gb": 10.0,
        "min_ram_gb": 18.0,
    },
    {
        "repo": HUGGING_FACE_MODEL_REPOS["gemma-4-E4B-it-GGUF"],
        "filename": "gemma-4-E4B-it-Q4_K_M.gguf",
        "label": "E4B Q4_K_M",
        "summary": "E4B 기본값으로 용량과 품질 균형이 좋습니다.",
        "min_vram_gb": 6.0,
        "min_ram_gb": 12.0,
    },
    {
        "repo": HUGGING_FACE_MODEL_REPOS["gemma-4-E4B-it-GGUF"],
        "filename": "gemma-4-E4B-it-IQ4_XS.gguf",
        "label": "E4B IQ4_XS",
        "summary": "E4B 경량 권장값입니다.",
        "min_vram_gb": 5.0,
        "min_ram_gb": 10.0,
    },
]


def fetch_huggingface_filenames() -> dict[str, set[str]]:
    filenames_by_repo: dict[str, set[str]] = {}

    for repo in HUGGING_FACE_MODEL_REPOS.values():
        api_url = f"https://huggingface.co/api/models/{repo}?expand[]=siblings"
        request = urllib.request.Request(api_url, headers=HUGGING_FACE_HEADERS)
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.load(response)

        siblings = payload.get("siblings", [])
        if not isinstance(siblings, list):
            filenames_by_repo[repo] = set()
            continue

        filenames: set[str] = set()
        for sibling in siblings:
            if isinstance(sibling, dict):
                rfilename = sibling.get("rfilename")
                if isinstance(rfilename, str):
                    filenames.add(rfilename)
        filenames_by_repo[repo] = filenames

    return filenames_by_repo


def build_huggingface_download_url(repo: str, filename: str) -> str:
    encoded_filename = urllib.parse.quote(filename, safe="/")
    return f"https://huggingface.co/{repo}/resolve/main/{encoded_filename}"


def detect_cuda_version(command_runner: Callable[[list[str]], str | None]) -> tuple[int, int] | None:
    nvcc_output = command_runner(["nvcc", "--version"])
    if nvcc_output:
        match = re.search(r"release\s+(\d+)\.(\d+)", nvcc_output)
        if match:
            return int(match.group(1)), int(match.group(2))

    nvidia_smi_output = command_runner(["nvidia-smi"])
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


def install_llama_cpp_assets(
    assets: list[dict[str, object]],
    runtime_dir: Path,
    render_progress: Callable[[str, Path, int, float | None], None] | None = None,
) -> None:
    runtime_dir.mkdir(parents=True, exist_ok=True)
    log_runtime_event(f"install_llama_cpp_assets start | runtime_dir={runtime_dir} | assets={len(assets)}")

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
                render_progress=(
                    None
                    if render_progress is None
                    else lambda current_asset, percent, speed_mbps: render_progress(
                        current_asset,
                        runtime_dir,
                        percent,
                        speed_mbps,
                    )
                ),
            )

            with ZipFile(archive_path) as archive:
                archive.extractall(extract_dir)
            log_runtime_event(
                f"llama asset extracted | asset={asset_name} | archive_path={archive_path} | extract_dir={extract_dir}"
            )

            source_root = get_single_root_or_self(extract_dir)
            for child in source_root.iterdir():
                destination = runtime_dir / child.name
                if child.is_dir():
                    shutil.copytree(child, destination, dirs_exist_ok=True)
                else:
                    shutil.copy2(child, destination)
            log_runtime_event(f"llama asset copied | asset={asset_name} | runtime_dir={runtime_dir}")

    server_path = runtime_dir / "llama-server.exe"
    if not server_path.is_file():
        raise RuntimeError("Downloaded llama.cpp asset did not contain llama-server.exe")


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


def run_command(command: list[str]) -> str | None:
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
