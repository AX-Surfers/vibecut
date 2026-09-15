#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy"]
# ///
"""이미 컷편집된 CapCut 타임라인에서, 경계 뒤 클립들의 '말 사이 멈춤'을 더 촘촘히 잘라낸다.

실전 배경: 사용자가 앞 3분을 CapCut에서 손으로 다듬고 "내가 한 거 반영해서 뒷부분 진행해"라고 했다.
앞부분을 재 보니 말 끝 뒤 0.04초 / 말 시작 앞 0.06초만 남기고, 0.7초 이상 멈춤은 전부,
0.5~0.7초는 대부분 잘라냈다(자동 컷은 문장 단위라 클립 안 멈춤이 최대 1.16초 남아 있었다).
--learn은 그 값들을 경계 앞 클립에서 직접 잰다.

- 경계 클립까지(0..N)의 본영상·오버레이·자막은 건드리지 않는다.
- 본영상과 1:1로 맞춰진 오버레이 비디오 트랙(얼굴캠 등)은 클립마다의 오프셋을 그대로 유지하며 같이 자른다.
- 경계 뒤 텍스트(자막) 세그먼트는 원본 시각을 따라 새 위치로 옮긴다 (잘린 멈춤만 줄어든다).
- 경계 뒤에 본영상과 맞춰지지 않은 트랙(오디오·스티커 등)이 있으면 옮길 수 없어 멈춘다.

사용:
  uv run tighten_pauses.py --project <CapCut 프로젝트> [--timeline "타임라인 01"] \\
      (--after-clip 58 | --backup <.vibecut_backups/…tar.gz>) [--learn] [--min-pause 0.5] [--dry-run]
"""
import argparse
import json
import math
import sys
import tarfile
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from _platform import check_capcut_not_running  # noqa: E402
from capcut_editor import (  # noqa: E402
    FPS,
    clone_video_segment,
    draft_path_for,
    frame_to_us,
    pick_video_track,
    resolve_timeline,
    write_4_files,
)
from speech import HOP, audio_for, inner_pauses, load_wav, speech_bounds, speech_mask  # noqa: E402

MIN_FRAMES = 13          # CapCut 최소 클립 길이 (capcut_editor.build_segments와 같음)
MIN_REMOVE_FRAMES = 6    # 0.2초보다 적게만 잘라낼 수 있으면 자르지 않는다
DEFAULT_STYLE = {"min_pause": 0.5, "keep_after": 0.04, "keep_before": 0.06}


def start_of(s: dict) -> int:
    return s["target_timerange"]["start"]


def end_of(s: dict) -> int:
    return s["target_timerange"]["start"] + s["target_timerange"]["duration"]


def plan_pieces(src_s: float, src_e: float, mask: np.ndarray, min_pause: float, keep_after: float,
                keep_before: float, fps: int = FPS) -> list[tuple[int, int]]:
    """원본 [src_s, src_e) 클립 하나를 가장자리 정적을 다듬고 멈춤에서 나눈 조각들 [(시작프레임, 끝프레임), ...]."""
    fs0, fe0 = round(src_s * fps), round(src_e * fps)
    bounds = speech_bounds(mask, src_s, src_e)
    if bounds is None:
        return [(fs0, fe0)]
    fs = max(fs0, math.floor((bounds[0] - keep_before) * fps + 1e-6))
    fe = min(fe0, math.ceil((bounds[1] + keep_after) * fps - 1e-6))
    if fe - fs < MIN_FRAMES:
        fs, fe = fs0, fe0
    pieces, cur = [], fs
    for ps, pe in inner_pauses(mask, src_s, src_e, min_pause):
        # 앞/뒤 조각이 CapCut 최소 길이보다 짧아지면 멈춤을 덜 자른다 (건너뛰면 긴 멈춤이 통째로 남음)
        a = max(math.ceil((ps + keep_after) * fps - 1e-6), cur + MIN_FRAMES)
        z = min(math.floor((pe - keep_before) * fps + 1e-6), fe - MIN_FRAMES)
        if z - a >= MIN_REMOVE_FRAMES:
            pieces.append((cur, a))
            cur = z
    pieces.append((cur, fe))
    return pieces


def learn_style(clips: list[tuple[float, float]], mask: np.ndarray) -> dict | None:
    """손으로 다듬은 클립들 [(원본 시작, 끝)]에서 말 앞/뒤 여유와 남겨 둔 멈춤 길이를 잰다."""
    lead, tail, longest = [], [], []
    for s, e in clips:
        b = speech_bounds(mask, s, e)
        if b is None:
            continue
        lead.append(b[0] - s)
        tail.append(e - b[1])
        longest.append(max((pe - ps for ps, pe in inner_pauses(mask, s, e, HOP)), default=0.0))
    if not lead:
        return None
    return {
        "keep_before": round(float(np.median(lead)), 2),
        "keep_after": round(float(np.median(tail)), 2),
        # 손편집에서 남겨 둔 클립별 최장 멈춤의 90%선 — 이보다 긴 멈춤은 사용자가 대개 잘랐다
        "min_pause": max(0.3, math.ceil(float(np.percentile(longest, 90)) * 10) / 10),
    }


def boundary_from_backup(mains: list[dict], backup: Path, timeline_uuid: str | None) -> int:
    """백업 이후 사용자가 손댄 마지막 클립 번호. 뒤에서부터 백업과 똑같은 클립을 걷어낸다 (-1 = 손댄 곳 없음)."""
    with tempfile.TemporaryDirectory() as tmp:
        with tarfile.open(backup) as tf:
            try:
                tf.extractall(tmp, filter="data")
            except TypeError:        # Python 3.11
                tf.extractall(tmp)
        drafts = list(Path(tmp).rglob("draft_info.json"))
        pick = [p for p in drafts if timeline_uuid and timeline_uuid in str(p)] or drafts
        old = json.loads(pick[0].read_text(encoding="utf-8"))
    oi = pick_video_track(old)
    old_ranges = {(s["source_timerange"]["start"], s["source_timerange"]["duration"]) for s in old["tracks"][oi]["segments"]}
    k = len(mains)
    while k > 0 and (mains[k - 1]["source_timerange"]["start"], mains[k - 1]["source_timerange"]["duration"]) in old_ranges:
        k -= 1
    return k - 1


def main():
    ap = argparse.ArgumentParser(description="경계 뒤 클립의 말 사이 멈춤을 촘촘히 자르기")
    ap.add_argument("--project", required=True, help="CapCut 프로젝트 디렉토리")
    ap.add_argument("--timeline", default=None, help="타임라인이 여러 개면 이름 또는 id 앞부분")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--after-clip", type=int, help="이 번호(0부터)까지의 클립은 그대로 둔다")
    g.add_argument("--backup", help="vibecut 백업 tar.gz — 그 뒤로 사용자가 손댄 마지막 클립을 경계로 삼는다")
    ap.add_argument("--learn", action="store_true", help="경계 앞(손편집) 클립에서 여유·멈춤 기준을 재서 쓴다")
    ap.add_argument("--min-pause", type=float, default=None, help=f"이 초 이상 멈춤을 자름 (기본 {DEFAULT_STYLE['min_pause']})")
    ap.add_argument("--keep-after", type=float, default=None, help=f"말 끝 뒤 남길 초 (기본 {DEFAULT_STYLE['keep_after']})")
    ap.add_argument("--keep-before", type=float, default=None, help=f"말 시작 앞 남길 초 (기본 {DEFAULT_STYLE['keep_before']})")
    ap.add_argument("--dry-run", action="store_true", help="계획만 출력하고 쓰지 않음")
    args = ap.parse_args()

    proj = Path(args.project).expanduser()
    tl, root = resolve_timeline(proj, args.timeline)
    dp = draft_path_for(proj, tl)
    if not args.dry_run:
        check_capcut_not_running()
    d = json.loads(dp.read_text(encoding="utf-8"))
    M = d["materials"]
    vids = {m["id"]: m for m in M.get("videos", [])}
    mi = pick_video_track(d)
    if mi is None:
        raise SystemExit("❌ 편집할 비디오 트랙을 찾지 못했습니다.")
    main_t = d["tracks"][mi]
    mains = sorted(main_t["segments"], key=start_of)

    if args.backup:
        n = boundary_from_backup(mains, Path(args.backup), tl)
        if n < 0:
            raise SystemExit("❌ 백업 이후 손댄 클립이 없습니다 — --after-clip으로 경계를 지정하세요.")
    else:
        n = args.after_clip
    if not 0 <= n < len(mains) - 1:
        raise SystemExit(f"❌ 경계 클립 번호가 범위 밖입니다 (0~{len(mains) - 2})")
    boundary = end_of(mains[n])
    print(f"✂ 경계: 클립 {n} (타임라인 {boundary / 1e6:.2f}초, 원본 "
          f"{(mains[n]['source_timerange']['start'] + mains[n]['source_timerange']['duration']) / 1e6:.2f}초) — 여기까지 그대로")

    overlays, blockers = [], []
    for t in d["tracks"]:
        if t is main_t or not t.get("segments"):
            continue
        segs = sorted(t["segments"], key=start_of)
        if t["type"] == "video" and len(segs) == len(mains) and all(
                a["target_timerange"] == b["target_timerange"] for a, b in zip(segs, mains)):
            overlays.append((t, segs))
        elif t["type"] != "text" and any(end_of(s) > boundary for s in segs):
            blockers.append(f"{t['type']} 트랙({len(segs)}개)")
    if blockers:
        raise SystemExit("❌ 경계 뒤에 본영상과 1:1로 맞춰지지 않은 트랙이 있어 같이 옮길 수 없습니다: " + ", ".join(blockers))

    video = Path(vids[mains[0]["material_id"]]["path"])
    x, sr = load_wav(audio_for(video))
    mask = speech_mask(x, sr)

    style = dict(DEFAULT_STYLE)
    if args.learn:
        learned = learn_style([(s["source_timerange"]["start"] / 1e6, end_src / 1e6) for s in mains[:n + 1]
                               for end_src in [s["source_timerange"]["start"] + s["source_timerange"]["duration"]]], mask)
        if learned:
            print(f"📏 손편집에서 잰 기준: 말 앞 {learned['keep_before']}초 / 말 뒤 {learned['keep_after']}초 / "
                  f"{learned['min_pause']}초 이상 멈춤 자름")
            style.update(learned)
    for key in style:
        if getattr(args, key) is not None:
            style[key] = getattr(args, key)

    back = mains[n + 1:]
    plan = []
    for k, seg in enumerate(back):
        s = seg["source_timerange"]["start"] / 1e6
        e = s + seg["source_timerange"]["duration"] / 1e6
        plan += [(k, a, b) for a, b in plan_pieces(s, e, mask, style["min_pause"], style["keep_after"], style["keep_before"])]
    kept = np.zeros(len(mask), bool)
    for _, a, b in plan:
        kept[int(a / FPS / HOP):math.ceil(b / FPS / HOP)] = True
    lost = sum((mask[i0:i1] & ~kept[i0:i1]).sum() for seg in back
               for i0, i1 in [(int(seg["source_timerange"]["start"] / 1e6 / HOP),
                               int((seg["source_timerange"]["start"] + seg["source_timerange"]["duration"]) / 1e6 / HOP))]) * HOP
    old_len = sum(s["source_timerange"]["duration"] for s in back) / 1e6
    new_len = sum(b - a for _, a, b in plan) / FPS
    print(f"   기준: {style['min_pause']}초 이상 멈춤 자름, 말 앞 {style['keep_before']}초 / 뒤 {style['keep_after']}초 남김")
    print(f"   뒷부분 클립 {len(back)} → {len(plan)}개 | {old_len:.0f}초 → {new_len:.0f}초 (-{old_len - new_len:.0f}초) | "
          f"잘린 말소리 {lost:.2f}초 | 전체 약 {(boundary / 1e6 + new_len) / 60:.1f}분 | 함께 옮길 오버레이 {len(overlays)}개")
    if args.dry_run:
        print("(dry-run — 쓰지 않음)")
        return

    front_snap = json.dumps([[(s["source_timerange"], s["target_timerange"]) for s in mains[:n + 1]]]
                            + [[(s["source_timerange"], s["target_timerange"]) for s in segs[:n + 1]] for _, segs in overlays])
    front_text = {s["id"]: json.dumps(s["target_timerange"]) for t in d["tracks"] if t["type"] == "text"
                  for s in t["segments"] if (start_of(s) + end_of(s)) / 2 < boundary}

    added: dict = {}
    new_main, new_ov = [], [[] for _ in overlays]
    piece_map, frames = [], 0
    for k, a, b in plan:
        tgt = boundary + frame_to_us(frames)
        src, dur = frame_to_us(a), frame_to_us(b) - frame_to_us(a)
        new_main.append(clone_video_segment(back[k], vids[back[k]["material_id"]], src, dur, tgt, added))
        for j, (_, segs) in enumerate(overlays):
            ov = segs[n + 1 + k]
            ov_src = src + ov["source_timerange"]["start"] - back[k]["source_timerange"]["start"]
            mat_dur = vids[ov["material_id"]].get("duration") or 0
            if ov_src < 0 or (mat_dur and ov_src + dur > mat_dur):
                raise SystemExit(f"❌ 오버레이가 소재 범위를 벗어납니다 (타임라인 {tgt / 1e6:.2f}초) — 쓰지 않고 중단")
            new_ov[j].append(clone_video_segment(ov, vids[ov["material_id"]], ov_src, dur, tgt, added))
        piece_map.append((src / 1e6, (src + dur) / 1e6, tgt / 1e6))
        frames += b - a
    assert all(p[1] <= q[0] + 1e-6 for p, q in zip(piece_map, piece_map[1:])), "원본 순서가 뒤섞인 클립은 지원하지 않음"

    old_map = [(start_of(s) / 1e6, end_of(s) / 1e6, s["source_timerange"]["start"] / 1e6) for s in back]

    def to_src(t: float) -> float:
        c = min(old_map, key=lambda c: 0 if c[0] - 1e-3 <= t <= c[1] + 1e-3 else min(abs(t - c[0]), abs(t - c[1])))
        return c[2] + min(max(t - c[0], 0.0), c[1] - c[0])

    def to_new(src: float, is_start: bool) -> float:
        for i, (ps, pe, pt) in enumerate(piece_map):
            if src <= pe + 1e-4:
                if src >= ps - 1e-4:
                    return pt + min(max(src - ps, 0.0), pe - ps)
                if is_start or i == 0:       # 잘려나간 멈춤 안 → 시작은 다음 조각 시작으로
                    return pt
                qs, qe, qt = piece_map[i - 1]  # 끝은 앞 조각 끝으로
                return qt + (qe - qs)
        qs, qe, qt = piece_map[-1]
        return qt + (qe - qs)

    b_s, dropped, dropped_mats = boundary / 1e6, 0, set()
    for t in d["tracks"]:
        if t["type"] != "text":
            continue
        keep = []
        for s in sorted(t["segments"], key=start_of):
            a, b = start_of(s) / 1e6, end_of(s) / 1e6
            if (a + b) / 2 < b_s:
                keep.append(s)
                continue
            na, nb = to_new(to_src(max(a, b_s)), True), to_new(to_src(b), False)
            if nb - na < 0.1:                 # 잘린 멈춤에만 걸쳐 있던 텍스트
                dropped += 1
                dropped_mats.add(s["material_id"])
                continue
            s["target_timerange"] = {"start": int(round(na * 1e6)), "duration": int(round((nb - na) * 1e6))}
            keep.append(s)
        for p, q in zip(keep, keep[1:]):
            if end_of(p) > start_of(q):
                p["target_timerange"]["duration"] = max(1, start_of(q) - start_of(p))
        t["segments"] = keep

    replaced = back + [s for _, segs in overlays for s in segs[n + 1:]]
    old_ids = {r for s in replaced for r in (s["material_id"], *s.get("extra_material_refs", []))} | dropped_mats
    main_t["segments"] = mains[:n + 1] + new_main
    for (t, segs), new in zip(overlays, new_ov):
        t["segments"] = segs[:n + 1] + new
    used = {r for t in d["tracks"] for s in t["segments"] for r in (s.get("material_id"), *s.get("extra_material_refs", []))}
    for key, lst in M.items():
        if isinstance(lst, list):
            M[key] = [m for m in lst if not (isinstance(m, dict) and m.get("id") in old_ids - used)]
    for key, lst in added.items():
        M.setdefault(key, []).extend(lst)
    d["duration"] = max(end_of(s) for t in d["tracks"] for s in t["segments"])
    print(f"   경계 뒤 텍스트 이동 (말 없는 멈춤에만 걸려 빠진 것 {dropped}개)")

    print("\n💾 파일 저장 중...")
    write_4_files(proj, tl, d, write_root=root)

    # ── 검증: 다시 읽어서 ──
    d2 = json.loads(dp.read_text(encoding="utf-8"))
    ids = {m["id"] for lst in d2["materials"].values() if isinstance(lst, list) for m in lst if isinstance(m, dict) and "id" in m}
    m2 = sorted(d2["tracks"][mi]["segments"], key=start_of)
    ov2 = [sorted(t["segments"], key=start_of) for t in d2["tracks"] if any(t["id"] == o["id"] for o, _ in overlays)]
    problems = []
    if json.dumps([[(s["source_timerange"], s["target_timerange"]) for s in m2[:n + 1]]]
                  + [[(s["source_timerange"], s["target_timerange"]) for s in segs[:n + 1]] for segs in ov2]) != front_snap:
        problems.append("경계 앞 클립이 바뀜")
    if any(abs(end_of(a) - start_of(b)) > 1 for a, b in zip(m2, m2[1:])):
        problems.append("본영상 클립 사이 틈/겹침")
    if any(len(segs) != len(m2) or any(a["target_timerange"] != b["target_timerange"] for a, b in zip(segs, m2)) for segs in ov2):
        problems.append("오버레이가 본영상과 1:1로 안 맞음")
    if any(r not in ids for t in d2["tracks"] for s in t["segments"] for r in (s["material_id"], *s.get("extra_material_refs", []))):
        problems.append("끊긴 소재 참조")
    for t in d2["tracks"]:
        if t["type"] == "text":
            ts = sorted(t["segments"], key=start_of)
            if any(end_of(a) > start_of(b) for a, b in zip(ts, ts[1:])):
                problems.append(f"텍스트 트랙 '{t.get('name', '')}' 겹침")
            if any(front_text.get(s["id"], json.dumps(s["target_timerange"])) != json.dumps(s["target_timerange"]) for s in ts):
                problems.append("경계 앞 텍스트가 바뀜")
    if problems:
        print("❌ 검증 실패: " + " / ".join(problems) + " — 백업에서 되돌리세요")
        sys.exit(1)
    print(f"✓ 경계 앞 그대로 / 본영상 {len(m2)}개 틈·겹침 0 / 오버레이 {len(ov2)}개 1:1 / 끊긴 참조 0 / 텍스트 겹침 0 "
          f"/ 전체 {d2['duration'] / 1e6 / 60:.2f}분")


if __name__ == "__main__":
    main()
