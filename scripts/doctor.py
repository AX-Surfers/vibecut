#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""
Vibecut — 환경 진단 및 초기화 (doctor).

uv·ffmpeg 확인, CapCut 프로젝트 디렉토리 탐지, ~/.vibecut/config.json 저장을
플랫폼(macOS/Windows)에 무관하게 순수 Python으로 수행한다.
셸 스크립트(bash)에 있던 로직을 여기로 옮겨 Codex CLI·Windows에서도 동일하게
동작하도록 한다.

사용법:
  uv run scripts/doctor.py
  python3 scripts/doctor.py   # uv 없이도 동작 (외부 의존성 없음)
"""

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

from _platform import capcut_projects_dir

CONFIG_PATH = Path.home() / ".vibecut" / "config.json"
SCRIPTS_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = SCRIPTS_DIR.parent


def _ok(msg: str) -> None:
    print(f"✅ {msg}")


def _warn(msg: str) -> None:
    print(f"⚠️  {msg}")


def _err(msg: str) -> None:
    print(f"❌ {msg}")


def check_uv() -> str | None:
    path = shutil.which("uv")
    if path:
        version = subprocess.run(["uv", "--version"], capture_output=True, text=True)
        _ok(f"uv: {version.stdout.strip()}")
        return path
    _warn("uv가 없습니다.")
    if sys.platform == "win32":
        print("   설치: powershell -c \"irm https://astral.sh/uv/install.ps1 | iex\"")
    else:
        print("   설치: curl -LsSf https://astral.sh/uv/install.sh | sh")
    return None


def check_ffmpeg() -> bool:
    path = shutil.which("ffmpeg")
    if path:
        version = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True)
        first_line = version.stdout.splitlines()[0] if version.stdout else "unknown"
        _ok(f"ffmpeg: {first_line}")
        return True
    _warn("ffmpeg 없음 — 자막 추가(모드 B, 편집 타임라인 기준)에 필요합니다.")
    if sys.platform == "win32":
        print("   설치: winget install ffmpeg  (또는 choco install ffmpeg)")
    elif sys.platform == "darwin":
        print("   설치: brew install ffmpeg")
    else:
        print("   설치: 배포판 패키지 매니저로 ffmpeg 설치")
    print("   ffmpeg가 없어도 무음 제거·NG 제거(vibecut-auto-edit)는 정상 동작합니다.")
    return False


def check_capcut_dir() -> Path:
    d = capcut_projects_dir()
    if d.exists():
        n = sum(1 for e in d.iterdir() if e.is_dir() and (e / "draft_info.json").exists())
        _ok(f"CapCut 프로젝트 디렉토리: {d} ({n}개 프로젝트)")
    else:
        _warn(f"CapCut 프로젝트 디렉토리를 찾을 수 없습니다: {d}")
        print("   CapCut에서 프로젝트를 하나 이상 만든 뒤 다시 실행하세요.")
        print("   경로가 다르다면 VIBECUT_CAPCUT_DIR 환경변수로 직접 지정할 수 있습니다.")
    return d


def sync_dependencies() -> None:
    if not shutil.which("uv"):
        _warn("uv가 없어 의존성 사전 설치를 건너뜁니다 (첫 실행 시 자동 설치됨).")
        return
    print("의존성 설치 중... (처음엔 1~2분 소요될 수 있습니다)")
    result = subprocess.run(
        ["uv", "sync"], cwd=PLUGIN_ROOT, capture_output=True, text=True
    )
    if result.returncode == 0:
        _ok("Python 의존성 설치 완료")
    else:
        _warn("uv sync 실패 — 각 스크립트 최초 실행 시 자동으로 설치됩니다.")
        print(result.stderr.strip()[-500:])


def write_config(capcut_dir: Path) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing: dict = {}
    if CONFIG_PATH.exists():
        try:
            existing = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    existing.update({
        "scripts_dir": str(SCRIPTS_DIR),
        "platform": sys.platform,
        "capcut_projects_dir": str(capcut_dir),
        "last_checked": time.strftime("%Y-%m-%d %H:%M:%S"),
    })
    CONFIG_PATH.write_text(
        json.dumps(existing, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    _ok(f"config 저장 완료: {CONFIG_PATH}")


def main() -> None:
    print(f"플랫폼: {sys.platform}")
    print(f"scripts 경로: {SCRIPTS_DIR}\n")

    check_uv()
    check_ffmpeg()
    capcut_dir = check_capcut_dir()
    print()
    sync_dependencies()
    print()
    write_config(capcut_dir)

    print("""
✅ Vibecut 설정 완료!

사용 가능한 스킬:
  /vibecut-auto-edit           — 무음 제거 · NG 감지 컷편집
  /vibecut-add-subtitles       — Whisper 한국어 자막 자동 생성
  /vibecut-youtube-description — 유튜브 제목·설명·챕터 생성
""")


if __name__ == "__main__":
    main()
