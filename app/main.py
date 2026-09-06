"""DesignReader — macOS 앱 진입점.

번들 안은 읽기 전용이므로 DB는 첫 실행 때
~/Library/Application Support/DesignReader/ 로 옮겨 쓴다.
"""
from __future__ import annotations

import os
import shutil
import socket
import sys
import threading
import time
from pathlib import Path

APP_NAME = "DesignReader"
OLD_NAMES = ["DiyeokyeonReader"]   # 이전 이름으로 저장된 자료를 물려받는다


def resource_dir() -> Path:
    """번들로 묶였을 때와 소스로 실행할 때 모두 통하는 리소스 경로."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent.parent


def user_data_dir() -> Path:
    base = Path.home() / "Library" / "Application Support"
    d = base / APP_NAME
    if not d.exists():
        # 이름을 바꾸기 전에 쌓아둔 번역을 잃지 않도록 옮겨온다
        for old in OLD_NAMES:
            src = base / old
            if src.exists():
                src.rename(d)
                break
    d.mkdir(parents=True, exist_ok=True)
    return d


def prepare_data() -> Path:
    """처음 실행할 때만 빈 교재함을 놓아준다.

    교재는 쓰는 사람이 직접 넣는 것이므로, 이미 쓰던 DB 가 있으면
    앱을 새로 깔아도 그대로 둔다.
    """
    dst = user_data_dir()
    db_dst = dst / "reader.db"
    if not db_dst.exists():
        seed = resource_dir() / "data" / "seed.db"
        if not seed.exists():
            seed = resource_dir() / "data" / "reader.db"
        if seed.exists():
            shutil.copy2(seed, db_dst)
    return dst


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def wait_until_up(port: int, timeout: float = 25.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            with socket.create_connection(("127.0.0.1", port), 0.4):
                return True
        except OSError:
            time.sleep(0.15)
    return False


def main() -> None:
    data = prepare_data()
    res = resource_dir()
    os.environ["READER_DATA"] = str(data)
    os.environ["READER_STATIC"] = str(res / "app" / "static")

    # 앱에서 실행하면 PATH가 빈약해 claude CLI를 못 찾는다. 흔한 경로를 보강한다.
    extra = [str(Path.home() / ".local/bin"), str(Path.home() / ".claude/local"),
             "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"]
    os.environ["PATH"] = os.pathsep.join(
        dict.fromkeys(os.environ.get("PATH", "").split(os.pathsep) + extra))

    port = free_port()

    import uvicorn
    from app.server import app as fastapi_app

    def serve():
        uvicorn.run(fastapi_app, host="127.0.0.1", port=port, log_level="warning")

    threading.Thread(target=serve, daemon=True).start()
    if not wait_until_up(port):
        print("서버를 시작하지 못했습니다", file=sys.stderr)
        sys.exit(1)

    import webview
    webview.create_window("DesignReader", f"http://127.0.0.1:{port}/",
                          width=1500, height=950, min_size=(1080, 680))
    webview.start()


if __name__ == "__main__":
    main()
