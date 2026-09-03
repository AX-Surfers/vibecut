#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""
Vibecut — 기존 CapCut 프로젝트에 자막 트랙 추가 (다른 트랙은 그대로)

예전에는 이 코드가 SKILL.md 안에 붙여넣기로 있어서 매번 다시 타이핑됐고,
`draft["tracks"] = [tracks[0], 새 자막]`으로 써서 배경음악·이미지 오버레이·
사용자가 직접 넣은 텍스트까지 전부 지워 버렸다. 이 스크립트는:
  - 비디오/오디오/기타 트랙을 전부 보존한다
  - vibecut이 만든 자막 트랙(name == "vibecut")만 교체한다. 그 밖의 텍스트 트랙은
    건드리지 않고 목록만 보여준다 (예전 버전이 만든 이름 없는 자막 트랙을 바꾸려면
    --replace-track N 으로 명시)
  - 4개 파일(루트 + Timelines/<uuid>) 동시 저장, 자동 백업, .locked 삭제

사용:
  uv run apply_subtitles.py --project <CapCut 프로젝트> [--timeline "타임라인 02"] \\
      --input <stem>_subtitle_input.json --splits <stem>_subtitle_splits.json
"""
import argparse
import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from add_subtitles import (  # noqa: E402
    DEFAULT_TEXT_MATERIAL,
    DEFAULT_TEXT_SEGMENT,
    apply_subtitle_outline,
    new_id,
    remove_overlaps,
    snap_to_frame,
    split_by_splits,
)
from capcut_editor import draft_path_for, resolve_timeline, write_4_files  # noqa: E402

TRACK_NAME = "vibecut"
SUBTITLE_Y = -0.7407407407407407   # 화면 하단 위치 (CapCut 좌표)


def build_text_track(parts: list[dict]) -> tuple[list[dict], dict]:
    """자막 파트 → (text materials, text track)."""
    materials, segments = [], []
    for render_idx, part in enumerate(parts):
        start_us, end_us = snap_to_frame(part["start"]), snap_to_frame(part["end"])
        if end_us <= start_us:
            continue
        mat = copy.deepcopy(DEFAULT_TEXT_MATERIAL)
        mat["id"] = new_id()
        content = json.loads(mat["content"])
        content["text"] = part["text"]
        for style in content.get("styles", []):
            style["range"] = [0, len(part["text"])]
        mat["content"] = json.dumps(content, ensure_ascii=False)
        apply_subtitle_outline(mat, border_width=0.15)
        materials.append(mat)

        seg = copy.deepcopy(DEFAULT_TEXT_SEGMENT)
        seg.update({
            "id": new_id(), "material_id": mat["id"],
            "extra_material_refs": [], "source_timerange": None,
            "target_timerange": {"start": start_us, "duration": end_us - start_us},
            "render_index": 14000 + render_idx,
        })
        seg.setdefault("clip", {}).setdefault("transform", {})["y"] = SUBTITLE_Y
        segments.append(seg)
    track = {"id": new_id(), "attribute": 0, "flag": 0, "is_default_name": False,
             "name": TRACK_NAME, "type": "text", "segments": segments}
    return materials, track


def replace_subtitle_track(draft: dict, materials: list[dict], track: dict,
                           replace_idx: int | None = None) -> list[str]:
    """vibecut 자막 트랙만 교체하고 나머지 트랙은 보존. 반환: 로그 줄들."""
    log = []
    keep, removed_mat_ids = [], set()
    for i, t in enumerate(draft["tracks"]):
        is_ours = t.get("type") == "text" and (t.get("name") == TRACK_NAME or i == replace_idx)
        if is_ours:
            removed_mat_ids |= {s.get("material_id") for s in t.get("segments", [])}
            log.append(f"  ↻ tracks[{i}] text '{t.get('name') or '(이름 없음)'}' {len(t.get('segments', []))}개 → 교체")
        else:
            keep.append(t)
            if t.get("type") == "text":
                log.append(f"  · tracks[{i}] text '{t.get('name') or '(이름 없음)'}' "
                           f"{len(t.get('segments', []))}개 보존 (교체하려면 --replace-track {i})")
            else:
                log.append(f"  · tracks[{i}] {t.get('type')} {len(t.get('segments', []))}개 보존")
    draft["tracks"] = keep + [track]
    texts = [m for m in draft["materials"].get("texts", []) if m.get("id") not in removed_mat_ids]
    draft["materials"]["texts"] = texts + materials
    return log


def main():
    ap = argparse.ArgumentParser(description="CapCut 프로젝트에 자막 트랙 추가 (다른 트랙 보존)")
    ap.add_argument("--project", required=True)
    ap.add_argument("--timeline", default=None, help="타임라인이 여러 개면 이름 또는 id 앞부분")
    ap.add_argument("--input", required=True, help="subtitle_input.json (타임라인 기준 시각)")
    ap.add_argument("--splits", required=True, help="subtitle_splits.json (텍스트 조각 배열, 1:1)")
    ap.add_argument("--replace-track", type=int, default=None,
                    help="이 인덱스의 텍스트 트랙도 교체 (예전 버전이 만든 이름 없는 자막 트랙)")
    ap.add_argument("--no-check", action="store_true", help="CapCut 실행 여부 확인 건너뜀")
    args = ap.parse_args()

    if not args.no_check:
        from _platform import check_capcut_not_running
        check_capcut_not_running()

    proj = Path(args.project).expanduser()
    timeline_uuid, write_root = resolve_timeline(proj, args.timeline)
    draft = json.loads(draft_path_for(proj, timeline_uuid).read_text(encoding="utf-8"))

    segments = json.loads(Path(args.input).read_text(encoding="utf-8"))
    splits = json.loads(Path(args.splits).read_text(encoding="utf-8"))
    if len(splits) != len(segments):
        raise SystemExit(f"❌ splits {len(splits)}개 ≠ 자막 단위 {len(segments)}개 (1:1이어야 함)")
    for i, (seg, parts) in enumerate(zip(segments, splits)):
        if not all(isinstance(p, str) for p in parts):
            raise SystemExit(f"❌ splits[{i}]는 텍스트 조각 배열이어야 합니다 (인덱스 아님): {parts!r}")
        if "".join(parts).replace(" ", "") != seg["text"].replace(" ", ""):
            print(f"  ⚠ splits[{i}] 글자가 원문과 다릅니다 — 분할 결과가 자막 텍스트로 쓰입니다:\n"
                  f"     원문: {seg['text']}\n     분할: {' | '.join(parts)}")
    for seg in segments:
        if "start" not in seg and seg.get("words"):
            seg["start"], seg["end"] = seg["words"][0]["start"], seg["words"][-1]["end"]

    parts = remove_overlaps(split_by_splits(segments, splits), min_gap=0.02)
    materials, track = build_text_track(parts)
    for line in replace_subtitle_track(draft, materials, track, args.replace_track):
        print(line)

    print("\n💾 파일 저장 중...")
    write_4_files(proj, timeline_uuid, draft, write_root=write_root)
    print(f"\n✓ 자막 {len(track['segments'])}개 추가 (트랙 '{TRACK_NAME}'), 다른 트랙 {len(draft['tracks']) - 1}개 보존")


if __name__ == "__main__":
    main()
