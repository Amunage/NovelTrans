from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

if __package__ in {None, ""}:
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

from app.settings.config import (
    DATA_ROOT,
)
from app.settings.logging import get_log_path, log_runtime_event
from app.settings.precheck import get_glossary_candidate_block_reason, get_refine_block_reason, get_translation_block_reason
from app.settings.update import clear_staged_update_files, get_startup_update_status
from app.extract.crawler import main as crawler_main
from app.settings.setup import ensure_llama_cpp_runtime, ensure_runtime_setup
from app.terms import main as glossary_main
from app.translation.base import main as translation_main
from app.translation.refine_existing import main as refine_existing_main
from app.translation.review import main as review_main
from app.ui.control import prompt_main_menu, wait_for_enter
from app.ui.render import render_main_menu
from app.ui.settings_flow import run_settings_menu
from app.utils.merge import main as merge_main

def main() -> int:
    try:
        log_runtime_event(f"main start | log_path={get_log_path()}")
        ensure_runtime_setup()
        status_message = get_startup_update_status()
        clear_staged_update_files()

        while True:
            choice = prompt_main_menu(status_message)

            if choice == "1":
                result = crawler_main()
                status_message = None
                if result == 130:
                    return 130
                continue

            if choice == "2":
                setup_message = ensure_llama_cpp_runtime(DATA_ROOT, confirm_install=True)
                if setup_message is not None:
                    status_message = setup_message
                    continue

                block_reason = get_translation_block_reason()
                if block_reason is not None:
                    status_message = block_reason
                    continue

                result = translation_main()
                status_message = None
                if result == 130:
                    return 130
                continue

            if choice == "3":
                setup_message = ensure_llama_cpp_runtime(DATA_ROOT, confirm_install=True)
                if setup_message is not None:
                    status_message = setup_message
                    continue

                block_reason = get_refine_block_reason()
                if block_reason is not None:
                    status_message = block_reason
                    continue

                result = refine_existing_main()
                status_message = None
                if result == 130:
                    return 130
                continue

            if choice == "4":
                result = review_main()
                status_message = None
                if result == 130:
                    return 130
                continue

            if choice == "5":
                result = merge_main()
                status_message = None
                if result == 130:
                    return 130
                continue

            if choice == "6":
                setup_message = ensure_llama_cpp_runtime(DATA_ROOT, confirm_install=True)
                if setup_message is not None:
                    status_message = setup_message
                    continue

                block_reason = get_glossary_candidate_block_reason()
                if block_reason is not None:
                    status_message = block_reason
                    continue

                result = glossary_main()
                status_message = None
                if result == 130:
                    return 130
                continue

            if choice == "7":
                status_message = run_settings_menu()
                if status_message == "__UPDATE_EXIT__":
                    return 0
                continue

            if choice == "=":
                return 0

            status_message = "[ERROR] 잘못된 입력입니다."
    except KeyboardInterrupt:
        log_runtime_event("main cancelled by user")
        os.system("cls")
        print("[INFO] 사용자가 작업을 중단했습니다.")
        return 130
    except Exception as exc:
        log_runtime_event(f"main unhandled error | error={exc!r}\n{traceback.format_exc()}")
        os.system("cls")
        print(f"[ERROR] {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
