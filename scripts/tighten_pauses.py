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

완성된 타임라인 후속 수정 (claude meeting 실전: 하이라이트 + 1.3배속 + 앞 142초엔 없는 얼굴캠 + 소재 2개):
- 배속: 클립마다 speed를 유지한다 (타임라인 길이 = 원본 길이 / speed).
- 오버레이가 일부 클립에만 있어도 된다 (각 조각이 본영상 클립 하나와 같은 자리이기만 하면).
  사용자가 CapCut에서 본영상 시작만 앞으로 늘려 오버레이가 뒤처진 경우는 같이 늘려 맞춘다.
- 본영상 소재가 여러 개면 클립마다 그 소재의 소리로 말소리를 잰다.
- --remove '[[a,b],...]': 멈춤 대신 원본 초 구간(NG)만 잘라낸다. 걸친 자막은 글자 확인 목록으로 출력.
- --silent-keep 1.2: 말이 전혀 없는 클립(화면만 보여 주는 장면)은 끝 1.2초만 남긴다 (기본: 통째로 남김).
- --noise-db: 소리를 키운(정규화한) 영상은 바닥 소음도 커져 기본 -45dB로는 멈춤을 못 찾는다 → -35 등.

사용:
  uv run tighten_pauses.py --project <CapCut 프로젝트> [--timeline "타임라인 01"] \\
      (--after-clip 58 | --backup <.vibecut_backups/…tar.gz>) [--learn] [--min-pause 0.5] \\
      [--remove '[[356.0,356.9]]'] [--silent-keep 1.2] [--noise-db -35] [--dry-run]
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
from speech import HOP, NOISE_DB, audio_for, inner_pauses, load_wav, speech_bounds, speech_mask  # noqa: E402

MIN_FRAMES = 13          # CapCut 최소 클립 길이 (capcut_editor.build_segments와 같음)
MIN_REMOVE_FRAMES = 6    # 0.2초보다 적게만 잘라낼 수 있으면 자르지 않는다
DEFAULT_STYLE = {"min_pause": 0.5, "keep_after": 0.04, "keep_before": 0.06}


def start_of(s: dict) -> int:
    return s["target_timerange"]["start"]


def end_of(s: dict) -> int:
    return s["target_timerange"]["start"] + s["target_timerange"]["duration"]


def same_span(a: dict, b: dict, tol_us: int = 1000) -> bool:
    """두 세그먼트가 같은 자리인지 (CapCut이 저장하며 1µs쯤 반올림하므로 오차를 준다)."""
    return abs(start_of(a) - start_of(b)) <= tol_us and \
        abs(a["target_timerange"]["duration"] - b["target_timerange"]["duration"]) <= tol_us


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


def subtract_ranges(src_s: float, src_e: float, remove: list, fps: int = FPS) -> list[tuple[int, int]]:
    """원본 [src_s, src_e)에서 remove 구간들 [(a, b), ...]을 뺀 조각들 [(시작프레임, 끝프레임), ...]."""
    pieces, cur = [], src_s
    for a, b in sorted(remove):
        if b > cur and a < src_e:
            if a > cur:
                pieces.append((round(cur * fps), round(a * fps)))
            cur = max(cur, b)
    if cur < src_e:
        pieces.append((round(cur * fps), round(src_e * fps)))
    return [(a, b) for a, b in pieces if b - a >= MIN_FRAMES]


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


def align_overlay(ov: dict, mains: list[dict]) -> dict | None:
    """오버레이 조각과 같은 자리의 본영상 클립. 사용자가 CapCut에서 본영상 시작만 앞으로 늘려
    오버레이가 뒤처졌으면(끝은 같고 시작만 클립 안쪽) 오버레이 시작을 같이 당겨 맞춘다."""
    for m in mains:
        if same_span(ov, m):
            return m
    for m in mains:
        if abs(end_of(ov) - end_of(m)) <= 1000 and start_of(m) < start_of(ov) < end_of(m):
            shift = start_of(ov) - start_of(m)
            sp = ov.get("speed", 1.0)
            if sp != 1.0:
                ov["source_timerange"]["start"] -= int(round(shift * sp))
                if ov["source_timerange"]["start"] < 0:
                    return None
            ov["target_timerange"] = dict(m["target_timerange"])
            ov["source_timerange"]["duration"] = int(round(m["target_timerange"]["duration"] * sp))
            print(f"   ↔ 오버레이 시작을 본영상에 맞춤 (타임라인 {start_of(m) / 1e6:.2f}초, {shift / 1e6:.2f}초 늘림)")
            return m
    return None


def main():
    ap = argparse.ArgumentParser(description="경계 뒤 클립의 말 사이 멈춤(또는 지정한 NG 구간)을 자르기")
    ap.add_argument("--project", required=True, help="CapCut 프로젝트 디렉토리")
    ap.add_argument("--timeline", default=None, help="타임라인이 여러 개면 이름 또는 id 앞부분")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--after-clip", type=int, help="이 번호(0부터)까지의 클립은 그대로 둔다 (하이라이트 등). -1 = 전부 대상")
    g.add_argument("--backup", help="vibecut 백업 tar.gz — 그 뒤로 사용자가 손댄 마지막 클립을 경계로 삼는다")
    ap.add_argument("--learn", action="store_true", help="경계 앞(손편집) 클립에서 여유·멈춤 기준을 재서 쓴다")
    ap.add_argument("--min-pause", type=float, default=None, help=f"이 초 이상 멈춤을 자름 (기본 {DEFAULT_STYLE['min_pause']})")
    ap.add_argument("--keep-after", type=float, default=None, help=f"말 끝 뒤 남길 초 (기본 {DEFAULT_STYLE['keep_after']})")
    ap.add_argument("--keep-before", type=float, default=None, help=f"말 시작 앞 남길 초 (기본 {DEFAULT_STYLE['keep_before']})")
    ap.add_argument("--remove", default=None, help="원본 초 구간 JSON '[[a,b],...]' — 주면 멈춤 대신 이 구간만 자른다 (NG 컷)")
    ap.add_argument("--silent-keep", type=float, default=None, help="말이 없는 클립은 끝 이 초만 남김 (기본: 통째로 남김)")
    ap.add_argument("--noise-db", type=float, default=NOISE_DB, help=f"말소리 판정 기준 dB (기본 {NOISE_DB}, 소리를 키운 영상은 -35)")
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
    if not -1 <= n < len(mains) - 1:
        raise SystemExit(f"❌ 경계 클립 번호가 범위 밖입니다 (-1~{len(mains) - 2})")
    boundary = end_of(mains[n]) if n >= 0 else 0
    print(f"✂ 경계: 클립 {n} (타임라인 {boundary / 1e6:.2f}초) — 여기까지 그대로")
    back = mains[n + 1:]
    speed = [s.get("speed", 1.0) for s in back]

    # 오버레이 비디오 트랙: 경계 뒤 조각마다 같은 자리의 본영상 클립을 찾는다 (일부 클립에만 있어도 됨)
    overlays, blockers = [], []
    for t in d["tracks"]:
        if t is main_t or not t.get("segments"):
            continue
        segs = sorted(t["segments"], key=start_of)
        if t["type"] == "video":
            owner = {}
            for s in segs:
                if end_of(s) <= boundary:
                    continue
                m = align_overlay(s, back)
                if m is None:
                    blockers.append(f"video 트랙 조각(타임라인 {start_of(s) / 1e6:.2f}초가 본영상 클립과 안 맞음)")
                    break
                owner[back.index(m)] = s
            overlays.append((t, segs, owner))
        elif t["type"] != "text" and any(end_of(s) > boundary for s in segs):
            blockers.append(f"{t['type']} 트랙({len(segs)}개)")
    if blockers:
        raise SystemExit("❌ 경계 뒤에 본영상과 맞춰지지 않은 트랙이 있어 같이 옮길 수 없습니다: " + ", ".join(blockers))

    masks: dict = {}

    def mask_for(seg: dict) -> np.ndarray:
        path = vids[seg["material_id"]]["path"]
        if path not in masks:
            x, sr = load_wav(audio_for(Path(path)))
            masks[path] = speech_mask(x, sr, args.noise_db)
        return masks[path]

    style = dict(DEFAULT_STYLE)
    if args.learn and n >= 0:
        learned = learn_style([(s["source_timerange"]["start"] / 1e6, end_src / 1e6) for s in mains[:n + 1]
                               for end_src in [s["source_timerange"]["start"] + s["source_timerange"]["duration"]]],
                              mask_for(mains[0]))
        if learned:
            print(f"📏 손편집에서 잰 기준: 말 앞 {learned['keep_before']}초 / 말 뒤 {learned['keep_after']}초 / "
                  f"{learned['min_pause']}초 이상 멈춤 자름")
            style.update(learned)
    for key in style:
        if getattr(args, key) is not None:
            style[key] = getattr(args, key)
    remove = json.loads(args.remove) if args.remove else None

    plan = []
    for k, seg in enumerate(back):
        s = seg["source_timerange"]["start"] / 1e6
        e = s + seg["source_timerange"]["duration"] / 1e6
        if remove is not None:
            plan += [(k, a, b) for a, b in subtract_ranges(s, e, remove)]
            continue
        mask = mask_for(seg)
        if args.silent_keep and speech_bounds(mask, s, e) is None and e - s > args.silent_keep:
            plan.append((k, round((e - args.silent_keep) * FPS), round(e * FPS)))   # 화면만 보여 주는 장면
            continue
        plan += [(k, a, b) for a, b in plan_pieces(s, e, mask, style["min_pause"], style["keep_after"], style["keep_before"])]

    # 타임라인 길이 = 원본 길이 / speed (프레임 단위)
    tframes = [max(1, round((b - a) / speed[k])) for k, a, b in plan]
    old_len = (end_of(back[-1]) - boundary) / 1e6
    new_len = sum(tframes) / FPS
    if remove is not None:
        print(f"   NG 구간 {len(remove)}개 자름")
    else:
        print(f"   기준: {style['min_pause']}초 이상 멈춤 자름, 말 앞 {style['keep_before']}초 / 뒤 {style['keep_after']}초 남김"
              + (f", 말 없는 클립은 끝 {args.silent_keep}초만" if args.silent_keep else ""))
    print(f"   뒷부분 클립 {len(back)} → {len(plan)}개 | 타임라인 {old_len:.1f}초 → {new_len:.1f}초 ({new_len - old_len:+.1f}초) | "
          f"전체 약 {(boundary / 1e6 + new_len) / 60:.2f}분 | 함께 옮길 오버레이 트랙 {len(overlays)}개")
    if args.dry_run:
        for k, a, b in plan:
            print(f"   클립{n + 1 + k:3d}  원본 {a / FPS:8.2f}–{b / FPS:8.2f}")
        print("(dry-run — 쓰지 않음)")
        return

    front_snap = json.dumps([(s["source_timerange"], s["target_timerange"]) for t in d["tracks"] if t["type"] == "video"
                             for s in sorted(t["segments"], key=start_of) if end_of(s) <= boundary])
    front_text = {s["id"]: json.dumps(s["target_timerange"]) for t in d["tracks"] if t["type"] == "text"
                  for s in t["segments"] if (start_of(s) + end_of(s)) / 2 < boundary}

    added: dict = {}
    new_main, new_ov = [], [[] for _ in overlays]
    piece_map, frames = [], 0   # (원본 시작, 원본 끝, 새 타임라인 시작, speed) 초
    for (k, a, _b), T in zip(plan, tframes):
        tgt = boundary + frame_to_us(frames)
        tdur = frame_to_us(frames + T) - frame_to_us(frames)
        src, sdur = frame_to_us(a), int(round(tdur * speed[k]))
        made = [clone_video_segment(back[k], vids[back[k]["material_id"]], src, sdur, tgt, added)]
        new_main.append(made[0])
        for j, (_, _, owner) in enumerate(overlays):
            ov = owner.get(k)
            if ov is None:
                continue
            osp = ov.get("speed", 1.0)
            mat = vids[ov["material_id"]]
            if mat.get("type") == "photo":     # 정지 이미지(배경 등)는 시작점이 의미 없음
                ov_src = ov["source_timerange"]["start"]
            else:
                ov_src = ov["source_timerange"]["start"] + int(round((src - back[k]["source_timerange"]["start"]) * osp / speed[k]))
            ov_dur = int(round(tdur * osp))
            mat_dur = mat.get("duration") or 0
            if ov_src < 0 or (mat_dur and mat.get("type") != "photo" and ov_src + ov_dur > mat_dur):
                raise SystemExit(f"❌ 오버레이가 소재 범위를 벗어납니다 (타임라인 {tgt / 1e6:.2f}초) — 쓰지 않고 중단")
            o = clone_video_segment(ov, mat, ov_src, ov_dur, tgt, added, draft_materials=M)   # 크로마키 등 효과 유지
            o["_speed"] = osp
            made.append(o)
            new_ov[j].append(o)
        for s in made:
            s["target_timerange"] = {"start": tgt, "duration": tdur}
            s["speed"] = s.pop("_speed", speed[k])
        piece_map.append((src / 1e6, (src + sdur) / 1e6, tgt / 1e6, speed[k]))
        frames += T
    assert all(p[1] <= q[0] + 1e-6 for p, q in zip(piece_map, piece_map[1:])), "원본 순서가 뒤섞인 클립은 지원하지 않음"
    sp_new = {m["id"]: m for m in added.get("speeds", [])}
    for s in new_main + [o for lst in new_ov for o in lst]:
        for r in s["extra_material_refs"]:
            if r in sp_new:
                sp_new[r]["speed"] = s["speed"]

    old_map = [(start_of(s) / 1e6, end_of(s) / 1e6, s["source_timerange"]["start"] / 1e6, s.get("speed", 1.0)) for s in back]

    def to_src(t: float) -> float:
        c = min(old_map, key=lambda c: 0 if c[0] - 1e-3 <= t <= c[1] + 1e-3 else min(abs(t - c[0]), abs(t - c[1])))
        return c[2] + min(max(t - c[0], 0.0), c[1] - c[0]) * c[3]

    def to_new(src: float, is_start: bool) -> float:
        for i, (ps, pe, pt, sp) in enumerate(piece_map):
            if src <= pe + 1e-4:
                if src >= ps - 1e-4:
                    return pt + min(max(src - ps, 0.0), pe - ps) / sp
                if is_start or i == 0:       # 잘려나간 구간 안 → 시작은 다음 조각 시작으로
                    return pt
                qs, qe, qt, qsp = piece_map[i - 1]  # 끝은 앞 조각 끝으로
                return qt + (qe - qs) / qsp
        qs, qe, qt, qsp = piece_map[-1]
        return qt + (qe - qs) / qsp

    texts = {m["id"]: m for m in M.get("texts", [])}
    b_s, dropped, dropped_mats, check = boundary / 1e6, 0, set(), []
    for t in d["tracks"]:
        if t["type"] != "text":
            continue
        keep = []
        for s in sorted(t["segments"], key=start_of):
            a, b = start_of(s) / 1e6, end_of(s) / 1e6
            if (a + b) / 2 < b_s:
                keep.append(s)
                continue
            sa, sb = to_src(max(a, b_s)), to_src(b)
            na, nb = to_new(sa, True), to_new(sb, False)
            if nb - na < 0.1:                 # 잘린 구간에만 걸쳐 있던 텍스트
                dropped += 1
                dropped_mats.add(s["material_id"])
                continue
            s["target_timerange"] = {"start": int(round(na * 1e6)), "duration": int(round((nb - na) * 1e6))}
            keep.append(s)
            if remove and any(ra < sb and rb > sa for ra, rb in remove) and s["material_id"] in texts:
                check.append((na, json.loads(texts[s["material_id"]]["content"]).get("text", "")))
        for p, q in zip(keep, keep[1:]):
            if end_of(p) > start_of(q):
                p["target_timerange"]["duration"] = max(1, start_of(q) - start_of(p))
        t["segments"] = keep

    replaced = back + [s for _, segs, _ in overlays for s in segs if end_of(s) > boundary]
    old_ids = {r for s in replaced for r in (s["material_id"], *s.get("extra_material_refs", []))} | dropped_mats
    main_t["segments"] = mains[:n + 1] + new_main
    for (t, segs, _), new in zip(overlays, new_ov):
        t["segments"] = [s for s in segs if end_of(s) <= boundary] + new
    used = {r for t in d["tracks"] for s in t["segments"] for r in (s.get("material_id"), *s.get("extra_material_refs", []))}
    for key, lst in M.items():
        if isinstance(lst, list):
            M[key] = [m for m in lst if not (isinstance(m, dict) and m.get("id") in old_ids - used)]
    for key, lst in added.items():
        M.setdefault(key, []).extend(lst)
    d["duration"] = max(end_of(s) for t in d["tracks"] for s in t["segments"])
    print(f"   경계 뒤 텍스트 이동 (잘린 구간에만 걸려 빠진 것 {dropped}개)")

    print("\n💾 파일 저장 중...")
    write_4_files(proj, tl, d, write_root=root)

    # ── 검증: 다시 읽어서 ──
    d2 = json.loads(dp.read_text(encoding="utf-8"))
    ids = {m["id"] for lst in d2["materials"].values() if isinstance(lst, list) for m in lst if isinstance(m, dict) and "id" in m}
    m2 = sorted(d2["tracks"][mi]["segments"], key=start_of)
    spans = {(start_of(s), s["target_timerange"]["duration"]) for s in m2}
    problems = []
    if json.dumps([(s["source_timerange"], s["target_timerange"]) for t in d2["tracks"] if t["type"] == "video"
                   for s in sorted(t["segments"], key=start_of) if end_of(s) <= boundary]) != front_snap:
        problems.append("경계 앞 클립이 바뀜")
    if any(abs(end_of(a) - start_of(b)) > 1 for a, b in zip(m2, m2[1:])):
        problems.append("본영상 클립 사이 틈/겹침")
    for t in d2["tracks"]:
        ts = sorted(t["segments"], key=start_of)
        if t["type"] == "video" and t["id"] != d2["tracks"][mi]["id"] and any(
                end_of(s) > boundary and (start_of(s), s["target_timerange"]["duration"]) not in spans for s in ts):
            problems.append("오버레이가 본영상 클립과 안 맞음")
        if t["type"] == "text":
            if any(end_of(a) > start_of(b) for a, b in zip(ts, ts[1:])):
                problems.append(f"텍스트 트랙 '{t.get('name', '')}' 겹침")
            if any(front_text.get(s["id"], json.dumps(s["target_timerange"])) != json.dumps(s["target_timerange"]) for s in ts):
                problems.append("경계 앞 텍스트가 바뀜")
    if any(r not in ids for t in d2["tracks"] for s in t["segments"] for r in (s["material_id"], *s.get("extra_material_refs", []))):
        problems.append("끊긴 소재 참조")
    if problems:
        print("❌ 검증 실패: " + " / ".join(problems) + " — 백업에서 되돌리세요")
        sys.exit(1)
    print(f"✓ 경계 앞 그대로 / 본영상 {len(m2)}개 틈·겹침 0 / 오버레이 맞음 / 끊긴 참조 0 / 텍스트 겹침 0 "
          f"/ 전체 {d2['duration'] / 1e6 / 60:.2f}분")
    if check:
        print("\n⚠ 잘린 NG에 걸쳐 있던 자막 — 글자에 잘린 말이 남았는지 확인하고 고치세요:")
        for na, txt in check:
            print(f"   {int(na // 60)}:{na % 60:05.2f}  {txt}")


if __name__ == "__main__":
    main()
