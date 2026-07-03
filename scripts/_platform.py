"""
Vibecut — 플랫폼별 CapCut 경로/프로세스 유틸리티.

macOS와 Windows에서 같은 스크립트가 동작하도록 경로 탐지와 프로세스 제어를
한 곳에 모은다. 모든 경로는 VIBECUT_CAPCUT_DIR 환경변수로 오버라이드 가능.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path


def _default_capcut_projects_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Movies/CapCut/User Data/Projects/com.lveditor.draft"
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        root = Path(base) if base else Path.home() / "AppData/Local"
        return root / "CapCut/User Data/Projects/com.lveditor.draft"
    # Linux 등 — CapCut 데스크탑 앱이 없는 플랫폼. VIBECUT_CAPCUT_DIR로만 지정 가능.
    return Path.home() / ".vibecut/unsupported-platform"


def capcut_projects_dir() -> Path:
    """CapCut 프로젝트 루트 디렉토리. VIBECUT_CAPCUT_DIR로 오버라이드 가능."""
    env = os.environ.get("VIBECUT_CAPCUT_DIR")
    return Path(env).expanduser() if env else _default_capcut_projects_dir()


def capcut_backup_root() -> Path:
    """자동 백업 저장 위치 (프로젝트 루트 옆 .vibecut_backups)."""
    return capcut_projects_dir() / ".vibecut_backups"


def is_capcut_running() -> bool:
    if sys.platform == "win32":
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq CapCut.exe"],
            capture_output=True, text=True,
        )
        return "CapCut.exe" in result.stdout
    result = subprocess.run(["pgrep", "-i", "capcut"], capture_output=True)
    return result.returncode == 0


def quit_capcut() -> None:
    """CapCut이 실행 중이면 종료. 아니면 조용히 통과."""
    if not is_capcut_running():
        print("  CapCut 실행 중 아님 — 계속 진행")
        return
    print("  CapCut 종료 중...")
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/IM", "CapCut.exe", "/F"], capture_output=True)
    else:
        subprocess.run(["pkill", "-x", "CapCut"])
    time.sleep(2)
    print("  CapCut 종료됨")


def check_capcut_not_running() -> None:
    """CapCut이 실행 중이면 안내 메시지 출력 후 종료(exit 1)."""
    if not is_capcut_running():
        return
    print("❌ CapCut이 실행 중입니다. 완전히 종료 후 다시 실행하세요.")
    if sys.platform == "win32":
        print("   작업 관리자에서 종료 후: tasklist | findstr -i capcut")
    else:
        print("   Cmd+Q 로 종료 후: ps aux | grep -i capcut | grep -v grep")
    sys.exit(1)


def default_subtitle_font_path() -> str:
    """
    템플릿 프로젝트에 texts 자막 자료가 하나도 없을 때만 쓰이는 fallback 폰트 경로.

    실사용에서는 거의 항상 템플릿의 실제 자막 material이 deepcopy되어 쓰이므로
    이 경로가 실제로 참조되는 일은 드물다. macOS 앱스토어판 CapCut의 폰트 캐시
    경로만 알려져 있어 그 외 플랫폼에서는 빈 문자열을 반환한다 — CapCut은 폰트
    경로가 비어 있으면 시스템 기본 폰트로 대체한다.
    """
    if sys.platform == "darwin":
        return str(
            Path.home() / "Library/Containers/com.lemon.lvoverseas/Data/Movies"
            "/CapCut/User Data/Cache/effect/7480847118538706181"
            "/892de34daab569720c6dbc43537e8cf5/font.ttf"
        )
    return ""


if __name__ == "__main__":
    # SKILL.md에서 raw pgrep/pkill 대신 호출하는 최소 CLI (Windows 호환 목적).
    #   uv run scripts/_platform.py quit-capcut
    #   uv run scripts/_platform.py check-not-running
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "quit-capcut":
        quit_capcut()
    elif cmd == "check-not-running":
        check_capcut_not_running()
    elif cmd == "print-projects-dir":
        print(capcut_projects_dir())
    else:
        print("사용법: _platform.py {quit-capcut|check-not-running|print-projects-dir}",
              file=sys.stderr)
        sys.exit(2)
