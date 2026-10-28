#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy"]
# ///
"""자막이 빠진 구간(말은 하는데 자막이 없는 곳)을 찾아 원본 전사에서 채워 넣는다.

실전 배경: 사용자가 CapCut에서 앞부분을 다시 컷편집하자 40초 구간의 자막이 통째로 사라졌고,
"자막 빈 공간이 있어, 추가해"라는 요청이 왔다. 손으로 21개를 채웠던 작업을 스크립트로 옮긴 것.

- 기존 자막은 건드리지 않고 빈 곳에만 새 자막을 끼워 넣는다 (모양은 기존 자막에서 그대로 가져온다).
- 글자는 원본 {stem}_words.json에서 가져오고, 오인식 사전(corrections.json)을 적용한다.
- 말소리 판정은 speech.py(-45dB)를 쓴다.

사용:
  uv run fill_subtitle_gaps.py --project <프로젝트> [--timeline "타임라인 01"] \\
      --words <영상>_words.json [--min-gap 0.4] [--max-chars 18] [--dry-run]
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from _platform import check_capcut_not_running  # noqa: E402
from apply_subtitles import TRACK_NAME, build_text_track, style_from_project  # noqa: E402
from capcut_editor import draft_path_for, pick_video_track, resolve_timeline, write_4_files  # noqa: E402
from make_transcript import detect_silences  # noqa: E402
from speech import HOP, audio_for, load_wav, speech_mask  # noqa: E402
from subtitles_from_cuts import (  # noqa: E402
    SNAP_MIN_SILENCE,
    SNAP_NOISE_DB,
    apply_corrections,
    snap_words_to_audio,
)


def start_of(s: dict) -> int:
    return s["target_timerange"]["start"]


def end_of(s: dict) -> int:
    return s["target_timerange"]["start"] + s["target_timerange"]["duration"]


def find_gaps(clips: list[tuple[float, float, float]], subs: list[tuple[float, float]],
              mask: np.ndarray, min_gap: float) -> list[tuple[float, float, float]]:
    """자막 없이 말소리만 있는 구간 [(타임라인 시작, 끝, 원본 시작)]."""
    covered = np.zeros(int(max((b for _, b, _ in clips), default=0) / HOP) + 2, bool)
    for a, b in subs:
        covered[int(a / HOP):int(b / HOP) + 1] = True
    gaps = []
    for cs, ce, ss in clips:
        n = int((ce - cs) / HOP)
        src0 = int(ss / HOP)
        speech = mask[src0:src0 + n]
        free = speech & ~covered[int(cs / HOP):int(cs / HOP) + len(speech)]
        i = 0
        while i < len(free):
            if not free[i]:
                i += 1
                continue
            j = i
            while j < len(free) and (free[j] or (j + 10 < len(free) and free[j:j + 10].any())):
                j += 1
            if (j - i) * HOP >= min_gap:
                gaps.append((cs + i * HOP, cs + j * HOP, ss + i * HOP))
            i = j
    return gaps


def words_in(words: list[dict], a: float, b: float) -> list[dict]:
    return [w for w in words if a <= (w["start"] + w["end"]) / 2 < b]


PARTICLES = {"를", "을", "이", "가", "은", "는", "에", "의", "도", "만", "로", "으로", "에서", "와", "과", "라고", "이라고"}


def join_words(ws: list[dict]) -> str:
    """단어들을 자막 한 줄로. Whisper가 조사를 따로 끊어 놓은 것("MCP 를")은 붙이고, 끝 문장부호는 뗀다
    (다른 자막에는 마침표가 없다)."""
    out = ""
    for w in ws:
        t = w["word"].strip()
        out = t if not out else (out + t if t in PARTICLES else out + " " + t)
    return out.strip().rstrip(".,")


def chunk(words: list[dict], max_chars: int) -> list[list[dict]]:
    """단어들을 max_chars 안쪽 덩어리로 (단어 중간에서 자르지 않는다)."""
    out: list[list[dict]] = []
    for w in words:
        if out and len(join_words(out[-1] + [w])) <= max_chars:
            out[-1].append(w)
        else:
            out.append([w])
    return out


def main():
    ap = argparse.ArgumentParser(description="자막이 빠진 구간을 원본 전사로 채우기")
    ap.add_argument("--project", required=True)
    ap.add_argument("--timeline", default=None)
    ap.add_argument("--words", required=True, help="원본 {stem}_words.json")
    ap.add_argument("--min-gap", type=float, default=0.4, help="이 초 이상 말하는데 자막이 없으면 채운다")
    ap.add_argument("--max-chars", type=int, default=18, help="자막 한 줄 최대 글자 수")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    proj = Path(args.project).expanduser()
    tl, root = resolve_timeline(proj, args.timeline)
    dp = draft_path_for(proj, tl)
    if not args.dry_run:
        check_capcut_not_running()
    d = json.loads(dp.read_text(encoding="utf-8"))
    vids = {m["id"]: m for m in d["materials"]["videos"]}
    tm = {m["id"]: m for m in d["materials"].get("texts", [])}
    main_t = d["tracks"][pick_video_track(d)]
    clips = sorted((start_of(s) / 1e6, end_of(s) / 1e6, s["source_timerange"]["start"] / 1e6)
                   for s in main_t["segments"])
    track = next((t for t in d["tracks"] if t.get("type") == "text" and t.get("name") == TRACK_NAME), None)
    if track is None:
        raise SystemExit("❌ vibecut 자막 트랙이 없습니다 — 먼저 apply_subtitles.py로 자막을 넣으세요")
    subs = sorted((start_of(s) / 1e6, end_of(s) / 1e6) for s in track["segments"])

    video = Path(vids[main_t["segments"][0]["material_id"]]["path"])
    x, sr = load_wav(audio_for(video))
    mask = speech_mask(x, sr)
    segments = json.loads(Path(args.words).read_text(encoding="utf-8"))
    snap_words_to_audio(segments, detect_silences(audio_for(video), noise_db=SNAP_NOISE_DB, min_dur=SNAP_MIN_SILENCE))
    n = apply_corrections(segments)      # 글자와 단어 양쪽에 오인식 사전 적용 (자막은 단어에서 만든다)
    if n:
        print(f"오인식 사전 적용: {n}개 문장 교정")
    words = [w for s in segments for w in s.get("words", []) if "start" in w]

    gaps = find_gaps(clips, subs, mask, args.min_gap)
    parts, empty = [], 0
    for ta, tb, sa in gaps:
        ws = words_in(words, sa, sa + (tb - ta))
        if not ws:
            empty += 1
            continue
        off = ta - sa
        for grp in chunk(ws, args.max_chars):
            a = max(ta, grp[0]["start"] + off)
            b = min(tb, grp[-1]["end"] + off)
            if b - a < 0.15:
                continue
            parts.append({"start": a, "end": b, "text": join_words(grp)})
    # 기존 자막·새 자막끼리 겹치지 않게 다듬기
    parts.sort(key=lambda p: p["start"])
    for p, q in zip(parts, parts[1:]):
        p["end"] = min(p["end"], q["start"])
    parts = [p for p in parts if p["end"] - p["start"] >= 0.15
             and not any(a < p["end"] and p["start"] < b for a, b in subs)]
    total = sum(b - a for a, b, _ in gaps)
    print(f"자막 없는 말소리 구간 {len(gaps)}곳, 총 {total:.1f}초 → 새 자막 {len(parts)}개"
          + (f" (글자를 못 찾은 구간 {empty}곳)" if empty else ""))
    for p in parts[:15]:
        print(f"  {int(p['start']//60):02d}:{p['start']%60:05.2f}~{p['end']%60:05.2f}  {p['text']}")
    if len(parts) > 15:
        print(f"  … 외 {len(parts) - 15}개")
    if not parts or args.dry_run:
        print("(dry-run — 쓰지 않음)" if args.dry_run else "채울 곳이 없습니다")
        return

    look = style_from_project(proj, args.timeline)      # 기존 자막과 같은 모양으로
    mats, new_track = build_text_track(parts, **look)
    base = max((s.get("render_index", 14000) for s in track["segments"]), default=14000) + 1
    layer = track["segments"][0].get("track_render_index", 1) if track["segments"] else 1
    for i, s in enumerate(new_track["segments"]):
        s["render_index"] = base + i
        s["track_render_index"] = layer
    track["segments"] = sorted(track["segments"] + new_track["segments"], key=start_of)
    d["materials"]["texts"] = list(tm.values()) + mats
    print("\n💾 파일 저장 중...")
    write_4_files(proj, tl, d, write_root=root)

    d2 = json.loads(dp.read_text(encoding="utf-8"))
    tm2 = {m["id"]: m for m in d2["materials"]["texts"]}
    t2 = sorted(next(t for t in d2["tracks"] if t.get("name") == TRACK_NAME)["segments"], key=start_of)
    problems = []
    if len(t2) != len(subs) + len(parts):
        problems.append(f"자막 수 {len(t2)} ≠ 기존 {len(subs)} + 새 {len(parts)}")
    if any(end_of(a) > start_of(b) for a, b in zip(t2, t2[1:])):
        problems.append("자막 겹침")
    if any(s["material_id"] not in tm2 for s in t2):
        problems.append("끊긴 자막 소재")
    if problems:
        print("❌ 검증 실패:", " / ".join(problems)); sys.exit(1)
    print(f"✓ 자막 {len(t2)}개 (새로 {len(parts)}개) / 겹침 0 / 끊긴 참조 0")


if __name__ == "__main__":
    main()
