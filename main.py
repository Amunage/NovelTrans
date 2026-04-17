from __future__ import annotations

import sys

from app.client import main as translation_main
from app.crawler import main as crawler_main
from app.ui import render_main_menu


def main() -> int:
    try:
        status_message = None

        while True:
            render_main_menu(status_message)
            choice = input("").strip()

            if choice == "1":
                result = crawler_main()
                status_message = None
                if result == 130:
                    return 130
                continue

            if choice == "2":
                result = translation_main()
                status_message = None
                if result == 130:
                    return 130
                continue

            if choice == "3":
                return 0

            status_message = "[ERROR] 잘못된 입력입니다."
    except KeyboardInterrupt:
        print("\n[INFO] 사용자가 작업을 중단했습니다.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
