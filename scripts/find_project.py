#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""
Vibecut — 영상이 들어 있는 CapCut 프로젝트/타임라인 찾기

"1.MP4를 컷편집해줘"라고 하면 그 파일을 불러온 프로젝트를 사용자에게 묻지 않고
찾아야 한다. 모든 프로젝트의 draft_info.json(루트 + Timelines/*)에서 소재 경로의
파일명을 대조한다.

사용법:
  uv run find_project.py 1.MP4            # 파일명(또는 경로)으로 검색
  uv run find_project.py --recent 5       # 최근 수정 프로젝트 목록
출력(검색): 한 줄에 하나 — <프로젝트 경로>\\t<타임라인 이름>\\t<타임라인 id>
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _platform import capcut_projects_dir  # noqa: E402
from capcut_editor import list_timelines  # noqa: E402


def video_names(draft_path: Path) -> set[str]:
    try:
        d = json.loads(draft_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    return {Path(m.get("path", "")).name.lower()
            for m in d.get("materials", {}).get("videos", []) if m.get("path")}


def find(video_name: str, root: Path) -> list[tuple[Path, str, str]]:
    target = Path(video_name).name.lower()
    hits = []
    for proj in sorted(root.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if not (proj / "draft_info.json").is_file():
            continue
        tls, _ = list_timelines(proj)
        if tls:
            for t in tls:
                if target in video_names(proj / "Timelines" / t["id"] / "draft_info.json"):
                    hits.append((proj, t["name"], t["id"]))
        elif target in video_names(proj / "draft_info.json"):
            hits.append((proj, "", ""))
    return hits


def main():
    ap = argparse.ArgumentParser(description="영상이 들어 있는 CapCut 프로젝트 찾기")
    ap.add_argument("video", nargs="?", help="영상 파일명 또는 경로")
    ap.add_argument("--recent", type=int, default=0, help="최근 수정된 프로젝트 N개 나열")
    args = ap.parse_args()

    root = capcut_projects_dir()
    if not root.is_dir():
        raise SystemExit(f"❌ CapCut 프로젝트 폴더가 없습니다: {root} (VIBECUT_CAPCUT_DIR로 지정 가능)")

    if args.recent:
        projs = [p for p in root.iterdir() if (p / "draft_info.json").is_file()]
        for p in sorted(projs, key=lambda p: p.stat().st_mtime, reverse=True)[:args.recent]:
            tls, _ = list_timelines(p)
            names = ", ".join(t["name"] or t["id"][:8] for t in tls) or "(단일)"
            print(f"{p}\t{names}")
        return

    if not args.video:
        ap.error("영상 파일명을 주거나 --recent N 을 쓰세요")
    hits = find(args.video, root)
    if not hits:
        print(f"❌ '{Path(args.video).name}'을(를) 불러온 CapCut 프로젝트가 없습니다. "
              "CapCut에서 영상을 먼저 가져와 주세요.")
        sys.exit(1)
    for proj, name, tid in hits:
        print(f"{proj}\t{name}\t{tid}")
    if len(hits) > 1:
        print(f"⚠ {len(hits)}곳에서 발견 — 최근 수정 순입니다. 어느 것인지 사용자에게 확인하세요.", file=sys.stderr)


if __name__ == "__main__":
    main()
