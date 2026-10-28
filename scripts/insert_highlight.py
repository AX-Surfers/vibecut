#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""영상 맨 앞에 '하이라이트' 구간을 끼워 넣는다 (인트로 예고편).

지정한 타임라인 구간들을 복사해 맨 앞에 순서대로 붙이고, 원래 있던 모든 클립·자막·오버레이를
하이라이트 길이만큼 뒤로 민다(리플). 복사본은 원본과 같은 트랙 구조를 따른다
(메인·얼굴캠·화면 녹화 등 모든 비디오 트랙, 그 구간의 자막). 원본 구간은 그대로 남는다.

- 이름이 'motion'인 오버레이 트랙은 복사하지 않고 뒤로만 민다.
- 재생 속도가 바뀐 클립은 지원하지 않는다.

사용:
  uv run insert_highlight.py --project <CapCut 프로젝트> [--timeline "타임라인 01"] \\
      --windows "297.0-299.4,317.2-319.6,..." [--dry-run]
"""
import argparse
import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _platform import check_capcut_not_running  # noqa: E402
from capcut_editor import (  # noqa: E402
    FPS,
    clone_video_segment,
    draft_path_for,
    frame_to_us,
    new_id,
    resolve_timeline,
    write_4_files,
)

MOTION = "motion"


def start_of(s: dict) -> int:
    return s["target_timerange"]["start"]


def end_of(s: dict) -> int:
    return start_of(s) + s["target_timerange"]["duration"]


def to_frame(us: int) -> int:
    return round(us * FPS / 1_000_000)


def main():
    ap = argparse.ArgumentParser(description="CapCut 맨 앞에 하이라이트 구간 끼워 넣기")
    ap.add_argument("--project", required=True)
    ap.add_argument("--timeline", default=None)
    ap.add_argument("--windows", required=True, help='"시작-끝,시작-끝,…" (타임라인 초). 이 순서대로 맨 앞에 붙는다')
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    wins = []
    for w in args.windows.split(","):
        a, b = (float(x) for x in w.split("-"))
        wins.append((round(a * FPS), round(b * FPS)))
    total = sum(b - a for a, b in wins)

    proj = Path(args.project).expanduser()
    tl, root = resolve_timeline(proj, args.timeline)
    dp = draft_path_for(proj, tl)
    if not args.dry_run:
        check_capcut_not_running()
    d = json.loads(dp.read_text(encoding="utf-8"))
    M = d["materials"]
    vids = {m["id"]: m for m in M.get("videos", [])}
    by_id = {m["id"]: (k, m) for k, lst in M.items() if isinstance(lst, list) for m in lst if isinstance(m, dict) and "id" in m}
    tracks = d["tracks"]

    for t in tracks:
        if t["type"] not in ("video", "text") and t["segments"]:
            raise SystemExit(f"❌ {t['type']} 트랙이 있어 같이 밀 수 없습니다")

    texts = {m["id"]: m for m in M.get("texts", [])}
    print(f"하이라이트 {len(wins)}구간, 합계 {total / FPS:.2f}초 → 전체가 그만큼 뒤로 밀립니다")
    off = 0
    for a, b in wins:
        caps = []
        for t in tracks:
            if t["type"] == "text":
                for s in t["segments"]:
                    if to_frame(start_of(s)) < b and to_frame(end_of(s)) > a:
                        mat = texts.get(s.get("material_id"))
                        caps.append(json.loads(mat["content"])["text"] if mat else "?")
        print(f"   {a / FPS:7.2f}~{b / FPS:7.2f}초 ({(b - a) / FPS:.1f}초)  자막: {' / '.join(caps) or '-'}")
        off += b - a
    if args.dry_run:
        print("(dry-run — 쓰지 않음)")
        return

    shift_us = frame_to_us(total)
    added: dict = {}
    new_per_track: dict[int, list] = {}
    cursor = 0
    for a, b in wins:
        for ti, t in enumerate(tracks):
            if t.get("name") == MOTION:
                continue
            for s in t["segments"]:
                sa, sb = to_frame(start_of(s)), to_frame(end_of(s))
                pa, pb = max(a, sa), min(b, sb)
                if pb - pa < 3:
                    continue
                dst = frame_to_us(cursor + pa - a)
                dur = frame_to_us(pb) - frame_to_us(pa)
                if t["type"] == "video":
                    if abs(s.get("speed", 1.0) - 1.0) > 1e-6:
                        raise SystemExit("❌ 재생 속도가 바뀐 클립은 지원하지 않습니다")
                    src = s["source_timerange"]["start"] + frame_to_us(pa) - start_of(s)
                    new = clone_video_segment(s, vids[s["material_id"]], src, dur, dst, added, draft_materials=M)
                else:  # 자막: 재료(텍스트·애니메이션 등)를 복제하고 구간만 잘라 붙인다
                    new = copy.deepcopy(s)
                    new["id"] = new_id()
                    refs = []
                    for ref in (s["material_id"], *s.get("extra_material_refs", [])):
                        if ref in by_id:
                            key, mat = by_id[ref]
                            clone = copy.deepcopy(mat)
                            clone["id"] = new_id()
                            added.setdefault(key, []).append(clone)
                            refs.append(clone["id"])
                    new["material_id"], new["extra_material_refs"] = refs[0], refs[1:]
                    new["target_timerange"] = {"start": dst, "duration": dur}
                new_per_track.setdefault(ti, []).append(new)
        cursor += b - a

    # 원래 클립을 뒤로 밀고, 하이라이트 복사본을 맨 앞에 붙인다
    for ti, t in enumerate(tracks):
        for s in t["segments"]:
            s["target_timerange"]["start"] += shift_us
        t["segments"] = sorted(new_per_track.get(ti, []), key=start_of) + sorted(t["segments"], key=start_of)
    for key, lst in added.items():
        M.setdefault(key, []).extend(lst)
    d["duration"] = max(end_of(s) for t in tracks for s in t["segments"])

    print("\n💾 파일 저장 중...")
    write_4_files(proj, tl, d, write_root=root)

    # 검증: 다시 읽어서
    d2 = json.loads(dp.read_text(encoding="utf-8"))
    ids = {m["id"] for lst in d2["materials"].values() if isinstance(lst, list) for m in lst if isinstance(m, dict) and "id" in m}
    problems = []
    for t in d2["tracks"]:
        ts = sorted(t["segments"], key=start_of)
        if any(end_of(x) > start_of(y) + 1 for x, y in zip(ts, ts[1:])):
            problems.append(f"{t['type']} 트랙 겹침")
        if any(r not in ids for s in ts for r in (s.get("material_id"), *s.get("extra_material_refs", [])) if r):
            problems.append(f"{t['type']} 트랙에 끊긴 소재 참조")
    first = next(t for t in d2["tracks"] if t["type"] == "video" and t.get("name") != MOTION)
    fs = sorted(first["segments"], key=start_of)
    if start_of(fs[0]) != 0 or any(abs(end_of(x) - start_of(y)) > 1 for x, y in zip(fs, fs[1:])):
        problems.append("첫 비디오 트랙에 틈/겹침")
    if problems:
        print("❌ 검증 실패: " + " / ".join(sorted(set(problems))) + " — 백업에서 되돌리세요")
        sys.exit(1)
    print(f"✓ 하이라이트 {total / FPS:.1f}초 삽입 / 틈·겹침 0 / 끊긴 참조 0 / 전체 {d2['duration'] / 1e6 / 60:.2f}분")


if __name__ == "__main__":
    main()
