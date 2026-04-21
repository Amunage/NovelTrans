from __future__ import annotations

import os
import sys
import traceback

from app.config import (
    get_glossary_candidate_block_reason,
    get_log_path,
    get_translation_block_reason,
    log_runtime_event,
)
from app.controller import run_settings_menu
from app.glossary import main as glossary_main
from app.config import APP_ROOT
from app.setup import ensure_llama_cpp_runtime, ensure_runtime_setup

from app.client import main as translation_main
from app.crawler import main as crawler_main
from app.ui import prompt_main_menu


def main() -> int:
    try:
        log_runtime_event(f"main start | log_path={get_log_path()}")
        ensure_runtime_setup()
        status_message = None

        while True:
            choice = prompt_main_menu(status_message)

            if choice == "1":
                result = crawler_main()
                status_message = None
                if result == 130:
                    return 130
                continue

            if choice == "2":
                setup_message = ensure_llama_cpp_runtime(APP_ROOT, confirm_install=True)
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
                setup_message = ensure_llama_cpp_runtime(APP_ROOT, confirm_install=True)
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

            if choice == "9":
                status_message = run_settings_menu()
                continue

            if choice == "0":
                return 0

            status_message = "[ERROR] 잘못된 입력입니다."
    except KeyboardInterrupt:
        log_runtime_event("main cancelled by user")
        os.system("cls")
        print("\n[INFO] 사용자가 작업을 중단했습니다.")
        return 130
    except Exception as exc:
        log_runtime_event(f"main unhandled error | error={exc!r}\n{traceback.format_exc()}")
        os.system("cls")
        print(f"[ERROR] {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
