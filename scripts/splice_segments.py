#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""
Vibecut — 부분 재편집(splice) 스크립트

사용자가 CapCut에서 타임라인 앞부분을 이미 손으로 다듬은 뒤, 나머지 구간만
새로 생성한 편집안으로 교체하고 싶을 때 사용합니다.

동작:
  1. 현재 CapCut 프로젝트(및 --timeline)의 draft_info.json에서 편집 대상 비디오
     트랙 세그먼트를 읽어 원본 영상 기준 source_timerange 목록을 복원합니다.
  2. --keep-until(원본 영상 기준 초) 또는 --keep-count(클립 개수)까지의
     구간은 사용자의 수동 편집 결과 그대로 보존합니다.
  3. --new-segments로 새로 생성한 segments.json에서 보존 구간 이후
     (start >= keep_until)만 골라 이어 붙입니다.
  4. 결과를 capcut_editor.py가 바로 읽을 수 있는 [[start, end], ...] 형식으로 저장합니다.

사용법:
  uv run splice_segments.py \\
    --project ~/Movies/CapCut/User\\ Data/Projects/com.lveditor.draft/0701 \\
    [--timeline "타임라인 02"] \\
    --keep-until 720.87 \\
    --new-segments <영상>_segments.json \\
    --out <영상>_segments_spliced.json

주의:
  - keep_until 근처에서 새 구간과 보존 구간이 겹치면 새 구간을 건너뜁니다
    (중복 재생 방지). 겹침이 걱정되면 --keep-until 값을 실제 보존 클립의
    끝 시각과 정확히 맞추세요.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from capcut_editor import draft_path_for, pick_video_track, resolve_timeline  # noqa: E402


def current_source_ranges(draft: dict) -> list[list[float]]:
    """현재 타임라인의 편집 대상 비디오 트랙 세그먼트를 원본 기준 [start, end](초)로 복원."""
    idx = pick_video_track(draft)
    if idx is None:
        raise SystemExit("❌ 편집할 비디오 트랙을 찾지 못했습니다.")
    ranges = []
    for s in draft["tracks"][idx]["segments"]:
        sr = s["source_timerange"]
        start = sr["start"] / 1_000_000
        ranges.append([start, start + sr["duration"] / 1_000_000])
    return ranges


def main():
    parser = argparse.ArgumentParser(description="부분 재편집 — 보존 구간 + 신규 구간 이어붙이기")
    parser.add_argument("--project", required=True, help="CapCut 프로젝트 디렉토리 경로")
    parser.add_argument("--timeline", default=None,
                        help="타임라인이 여러 개면 이름(예: '타임라인 02') 또는 id 앞부분")
    parser.add_argument("--keep-until", type=float, default=None,
                        help="이 시각(원본 영상 기준 초)까지의 현재 클립을 그대로 보존")
    parser.add_argument("--keep-count", type=int, default=None,
                        help="현재 타임라인의 앞에서부터 N개 클립을 그대로 보존 (--keep-until 대신 사용)")
    parser.add_argument("--new-segments", required=True,
                        help="새로 생성한 segments.json 경로 ([[start,end],...])")
    parser.add_argument("--out", default=None,
                        help="출력 경로 (기본: <new-segments 이름>_spliced.json)")
    args = parser.parse_args()

    if args.keep_until is None and args.keep_count is None:
        print("❌ --keep-until 또는 --keep-count 중 하나를 지정하세요.")
        sys.exit(1)

    project_dir = Path(args.project).expanduser()
    if not project_dir.exists():
        print(f"❌ 프로젝트 디렉토리가 없습니다: {project_dir}")
        sys.exit(1)

    timeline_uuid, _ = resolve_timeline(project_dir, args.timeline)
    draft = json.loads(draft_path_for(project_dir, timeline_uuid).read_text(encoding="utf-8"))
    cur = current_source_ranges(draft)

    if args.keep_count is not None:
        kept = cur[:args.keep_count]
    else:
        kept = [r for r in cur if r[1] <= args.keep_until + 1e-3]

    if not kept:
        print("⚠ 보존할 클립이 0개입니다 — keep-until/keep-count 값을 확인하세요.")

    boundary = kept[-1][1] if kept else 0.0
    print(f"📌 보존 클립: {len(kept)}개 (원본 영상 기준 0~{boundary:.2f}s)")

    new_path = Path(args.new_segments)
    new_segs = json.loads(new_path.read_text(encoding="utf-8"))

    tail = [s for s in new_segs if s[0] >= boundary - 1e-3]
    skipped = len(new_segs) - len(tail)
    if skipped:
        print(f"   → 신규 구간 중 경계와 겹치는 {skipped}개는 건너뜀 (중복 방지)")
    print(f"📌 신규 클립: {len(tail)}개 ({boundary:.2f}s~)")

    combined = kept + tail
    total = sum(e - s for s, e in combined)
    print(f"✅ 합계: {len(combined)}개 클립, {total:.1f}초 ({total/60:.1f}분)")

    out = Path(args.out) if args.out else new_path.with_name(new_path.stem + "_spliced.json")
    out.write_text(json.dumps(combined), encoding="utf-8")
    print(f"저장: {out}")
    print("→ capcut_editor.py 로 전달하세요.")


if __name__ == "__main__":
    main()
