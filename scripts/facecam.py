#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy"]
# ///
"""얼굴캠(DJI 등 별도 카메라) 영상을 화면녹화 컷에 소리로 싱크 맞춰 PIP로 올린다.

전제: 얼굴캠 파일은 CapCut에서 가져와 아무 비디오 트랙에 한 번 올려 둔다 (미디어 가져오기는 GUI 작업).
그 트랙이 본영상 컷과 아직 안 맞춰져 있으면 컷마다 조각을 새로 만들고, 이미 1:1로 맞춰져 있으면
각 조각의 원본 시작점만 다시 맞춘다.

실전 교훈:
- 싱크는 소리로 잰다. 파일 생성 시각으로는 42초 차이였지만 실제로는 1.4초였다.
- 두 기기의 시계는 실제로 다르게 간다. DJI가 약 40ppm 느려 37분 동안 오프셋이 1.43→1.34초로 줄었다.
  한때 "전 구간 1.40초로 일정"이라고 잘못 결론 내리고 보정을 없앴는데, 탐색 범위를 한쪽(1.40~1.60초)으로만
  잡아 결과가 아래 끝(1.400)에 붙었기 때문이었다. 그래서 양쪽 ±SEARCH초로 찾고, 끝에 붙은 창은 버리고,
  여러 곳의 값을 직선으로 맞춘다. 직선에서 크게 벗어난 창이 있으면(녹화 끊김 의심) 멈춘다.
- CapCut은 저장할 때 원본 시작점을 30fps 칸에 맞춰 버려 오프셋이 1/30초 단위로만 남았다(최대 ~22ms 틀어짐).
  그래서 가장 가까운 프레임으로 직접 반올림해 넣는다 — 한 프레임(33ms)보다 정밀한 싱크는 불가능하다.
- 소리 선택: 화면녹화 오디오는 노이즈 게이트로 칸의 46%가 완전무음(음절마다 뚝뚝 끊김), DJI는 0%였다.
- CapCut은 소재를 캔버스 안에 맞춰 넣으므로(scale 1 = 캔버스에 꽉 맞춤) 크기는 그 기준의 비율로 준다.

사용:
  uv run facecam.py --project <CapCut 프로젝트> [--timeline "타임라인 01"] --facecam DJI_xxx.MP4 \\
      [--corner br] [--height 0.35] [--margin 0] [--audio auto|facecam|screen] \\
      [--from-clip N] [--place-only] [--dry-run]
"""
import argparse
import json
import subprocess
import sys
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
from speech import HOP, audio_for, load_wav, speech_mask  # noqa: E402

SEARCH = 0.3               # 대략값 기준 앞뒤로 찾는 범위(초)
SYNC_WIN = 20.0            # 싱크를 재는 창 길이(초)
SYNC_WINDOWS = 8           # 녹화 전체에 고르게 놓는 창 수
SYNC_RESIDUAL_MAX = 0.025  # 직선에서 이만큼(초) 넘게 벗어난 창이 있으면 멈춘다
OUTLIER_MS = 300           # --from-clip 앞 조각은 이만큼 넘게 어긋난 것만 고친다
GATING_GAP = 0.15          # 완전무음 칸 비율이 이만큼 더 높은 쪽 소리는 쓰지 않는다


def start_of(s: dict) -> int:
    return s["target_timerange"]["start"]


def envelope(x: np.ndarray, sr: int, hop_s: float) -> np.ndarray:
    """말의 리듬(로그 에너지 포락선). 마이크가 달라도 같은 말이면 모양이 같다."""
    h = max(1, int(sr * hop_s))
    n = len(x) // h
    e = np.log1p(np.sqrt((x[: n * h].reshape(n, h) ** 2).mean(1)) * 32768)
    return (e - e.mean()) / (e.std() + 1e-9)


def xcorr_lag(ref: np.ndarray, other: np.ndarray, hop_s: float) -> tuple[float, float]:
    """ref[t + lag] ≈ other[t] 가 되는 lag(초)와 두드러짐(최고값 ÷ 1초 밖 최고값)."""
    size = 1 << int(np.ceil(np.log2(len(ref) + len(other))))
    c = np.fft.irfft(np.fft.rfft(ref, size) * np.conj(np.fft.rfft(other, size)), size)
    lags = np.r_[np.arange(0, size // 2), np.arange(-size // 2, 0)] * hop_s
    k = int(np.argmax(c))
    far = c[np.abs(lags - lags[k]) > 1.0]
    return float(lags[k]), float(c[k] / far.max()) if far.size and far.max() > 0 else float("inf")


def slide_match(ref: np.ndarray, other: np.ndarray) -> int:
    """더 짧은 other를 ref 위에서 밀어 가며 가장 잘 맞는 시작 칸."""
    n = len(other)
    return int(np.argmax([float(np.dot(ref[k:k + n], other)) for k in range(len(ref) - n + 1)]))


def fit_sync(points: list[tuple[float, float]]) -> tuple[float, float, float]:
    """[(화면녹화 시각, 오프셋)] → (절편, 기울기, 직선에서 가장 크게 벗어난 값). 오프셋(t) = 절편 + 기울기·t"""
    t, o = np.array(points).T
    slope, icpt = np.polyfit(t, o, 1)
    return float(icpt), float(slope), float(np.abs(o - (icpt + slope * t)).max())


def facecam_start_us(main_src_us: int, dur_us: int, icpt: float, slope: float, fps: int = FPS) -> int:
    """본영상 조각에 대응하는 얼굴캠 원본 시작점. 조각 가운데 시각의 오프셋을 쓰고 프레임에 반올림한다
    (CapCut이 저장하며 어차피 프레임에 맞추므로, 가장 가까운 프레임을 직접 고른다)."""
    mid = (main_src_us + dur_us / 2) / 1e6
    return frame_to_us(round((main_src_us / 1e6 + icpt + slope * mid) * fps))


def corner_transform(canvas_w: float, canvas_h: float, media_w: float, media_h: float,
                     height_frac: float, margin_px: float, corner: str) -> tuple[float, float, float, float, float]:
    """(scale, transform.x, transform.y, 표시 너비px, 표시 높이px). transform 단위는 캔버스 절반 = 1."""
    fit = min(canvas_w / media_w, canvas_h / media_h)
    scale = height_frac * canvas_h / (media_h * fit)
    w, h = media_w * fit * scale, media_h * fit * scale
    tx = (canvas_w / 2 - margin_px - w / 2) / (canvas_w / 2)
    ty = (canvas_h / 2 - margin_px - h / 2) / (canvas_h / 2)
    return scale, tx if corner[1] == "r" else -tx, -ty if corner[0] == "b" else ty, w, h


def extract_audio(video: Path, start: float, dur: float, sr: int, tmpdir: str) -> np.ndarray:
    out = Path(tmpdir) / f"fc_{start:.3f}.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{max(start, 0):.3f}", "-t", f"{dur:.3f}", "-i", str(video),
                    "-vn", "-ac", "1", "-ar", str(sr), str(out)], check=True)
    return load_wav(out)[0]


def measure_sync(main_x: np.ndarray, sr: int, facecam: Path, facecam_dur: float, tmpdir: str) -> list[tuple[float, float]]:
    """녹화 곳곳에서 잰 [(화면녹화 시각, 얼굴캠 시각 - 화면녹화 시각)]. 앞 5분으로 대략 잡고 창마다 양쪽으로 찾는다."""
    main_dur = len(main_x) / sr
    span = min(300.0, facecam_dur, main_dur)
    fc = extract_audio(facecam, 0, span, sr, tmpdir)
    lag, prominence = xcorr_lag(envelope(main_x[: int(min(main_dur, span + 180) * sr)], sr, 0.01), envelope(fc, sr, 0.01), 0.01)
    if prominence < 1.5:
        raise SystemExit(f"❌ 두 영상의 소리가 뚜렷하게 겹치지 않습니다 (두드러짐 {prominence:.2f}) — 같은 촬영인지 확인하세요")
    coarse, hop = -lag, 0.0025
    mask = speech_mask(main_x, sr)
    points = []
    for t in np.linspace(0.03 * main_dur, 0.97 * main_dur - SYNC_WIN, SYNC_WINDOWS):
        fs = t + coarse - SEARCH
        if fs < 0 or fs + SYNC_WIN + 2 * SEARCH > facecam_dur or mask[int(t / HOP):int((t + SYNC_WIN) / HOP)].mean() < 0.3:
            continue
        ref = envelope(extract_audio(facecam, fs, SYNC_WIN + 2 * SEARCH, sr, tmpdir), sr, hop)
        other = envelope(main_x[int(t * sr): int((t + SYNC_WIN) * sr)], sr, hop)
        k = slide_match(ref, other)
        if k in (0, len(ref) - len(other)):      # 탐색 끝에 붙음 = 범위 밖 → 이 창은 믿지 않는다
            continue
        points.append((t + SYNC_WIN / 2, coarse - SEARCH + k * hop))
    if len(points) < 3:
        raise SystemExit("❌ 싱크를 잴 수 있는 구간이 3곳 미만입니다 (말이 적거나 겹치는 구간이 짧음)")
    return points


def gating_ratio(x: np.ndarray, sr: int) -> float:
    """완전무음(-80dB 미만) 칸 비율 — 노이즈 게이트가 말 사이를 잘라먹는 정도."""
    h = int(sr * 0.02)
    n = len(x) // h
    db = 20 * np.log10(np.sqrt((x[: n * h].reshape(n, h) ** 2).mean(1)) + 1e-12)
    return float((db < -80).mean())


def main():
    ap = argparse.ArgumentParser(description="얼굴캠을 소리로 싱크 맞춰 PIP로 올리기")
    ap.add_argument("--project", required=True, help="CapCut 프로젝트 디렉토리")
    ap.add_argument("--timeline", default=None, help="타임라인이 여러 개면 이름 또는 id 앞부분")
    ap.add_argument("--facecam", required=True, help="CapCut에 올려 둔 얼굴캠 파일 이름 또는 경로")
    ap.add_argument("--corner", choices=["br", "bl", "tr", "tl"], default="br", help="붙일 모서리 (기본 오른쪽 아래)")
    ap.add_argument("--height", type=float, default=0.35, help="캔버스 높이 대비 표시 높이 (기본 0.35)")
    ap.add_argument("--margin", type=float, default=0.0, help="모서리에서 띄울 px (기본 0 = 딱 붙임)")
    ap.add_argument("--audio", choices=["auto", "facecam", "screen"], default=None,
                    help="쓸 소리 (기본 auto, --place-only면 건드리지 않음)")
    ap.add_argument("--from-clip", type=int, default=0,
                    help=f"이 번호 앞 조각은 {OUTLIER_MS}ms 넘게 어긋난 것만 고친다 (손편집한 앞부분 보호)")
    ap.add_argument("--place-only", action="store_true", help="싱크는 그대로 두고 위치·크기만 바꿈")
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
    mains = sorted(d["tracks"][mi]["segments"], key=start_of)
    fc_name = Path(args.facecam).name
    fc_tracks = [t for i, t in enumerate(d["tracks"]) if i != mi and t["type"] == "video" and t.get("segments")
                 and all(Path(vids[s["material_id"]]["path"]).name == fc_name for s in t["segments"])]
    if len(fc_tracks) != 1:
        raise SystemExit(f"❌ '{fc_name}'만 올라간 비디오 트랙을 {len(fc_tracks)}개 찾았습니다 — "
                         "CapCut에서 얼굴캠을 가져와 트랙 하나에 올려 주세요")
    fc_t = fc_tracks[0]
    fc_segs = sorted(fc_t["segments"], key=start_of)
    fc_mat = vids[fc_segs[0]["material_id"]]
    fc_path, fc_dur_us = Path(fc_mat["path"]), fc_mat["duration"]
    aligned = len(fc_segs) == len(mains) and all(a["target_timerange"] == b["target_timerange"] for a, b in zip(fc_segs, mains))

    cw, ch = d["canvas_config"]["width"], d["canvas_config"]["height"]
    scale, tx, ty, w, h = corner_transform(cw, ch, fc_mat["width"], fc_mat["height"], args.height, args.margin, args.corner)
    print(f"🎥 얼굴캠 {fc_name} | 트랙 조각 {len(fc_segs)}개 (본영상 {len(mains)}개와 {'1:1' if aligned else '안 맞음 → 새로 만듦'})")
    print(f"   위치: {args.corner} 모서리, 여백 {args.margin:g}px, {w:.0f}x{h:.0f}px (scale {scale:.4f}, x {tx:.4f}, y {ty:.4f})")

    sync, choice = None, args.audio if args.place_only else None
    if not args.place_only:
        main_video = Path(vids[mains[0]["material_id"]]["path"])
        x, sr = load_wav(audio_for(main_video))
        with tempfile.TemporaryDirectory() as tmp:
            points = measure_sync(x, sr, fc_path, fc_dur_us / 1e6, tmp)
            icpt, slope, worst = fit_sync(points)
            print("   싱크 측정: " + ", ".join(f"{t:.0f}s→{o:.3f}" for t, o in points))
            if worst > SYNC_RESIDUAL_MAX:
                raise SystemExit(f"❌ 직선에서 {worst * 1000:.0f}ms 벗어난 구간이 있습니다 — 녹화 중 끊김 의심, 자동으로 맞추지 않습니다")
            sync = (icpt, slope)
            t_end = len(x) / sr
            print(f"   싱크: 얼굴캠 = 화면녹화 + {icpt + slope * 0:.3f}초(시작) → {icpt + slope * t_end:.3f}초(끝) "
                  f"| 시계 차이 {slope * 1e6:+.0f}ppm | 직선 오차 최대 {worst * 1000:.0f}ms")
            choice = args.audio or "auto"
            if choice == "auto":
                t = 0.15 * t_end
                g_main = gating_ratio(x[int(t * sr): int((t + 60) * sr)], sr)
                g_fc = gating_ratio(extract_audio(fc_path, t + icpt + slope * t, 60, sr, tmp), sr)
                choice = "facecam" if g_main - g_fc > GATING_GAP else "screen"
                print(f"   소리: 완전무음 칸 화면녹화 {g_main:.0%} / 얼굴캠 {g_fc:.0%} → {'얼굴캠' if choice == 'facecam' else '화면녹화'} 소리 사용")

    added, resynced, skipped = {}, [], 0
    if sync and not aligned:
        tmpl, new = fc_segs[0], []
        for i, m in enumerate(mains):
            dur = m["target_timerange"]["duration"]
            src = facecam_start_us(m["source_timerange"]["start"], dur, *sync)
            if src < 0 or src + dur > fc_dur_us:
                skipped += 1
                continue
            new.append(clone_video_segment(tmpl, fc_mat, src, dur, start_of(m), added))
            resynced.append(i)
        old_ids = {r for s in fc_segs for r in (s["material_id"], *s.get("extra_material_refs", []))}
        fc_t["segments"] = new
        used = {r for t in d["tracks"] for s in t["segments"] for r in (s.get("material_id"), *s.get("extra_material_refs", []))}
        for key, lst in M.items():
            if isinstance(lst, list):
                M[key] = [m for m in lst if not (isinstance(m, dict) and m.get("id") in old_ids - used)]
        for key, lst in added.items():
            M.setdefault(key, []).extend(lst)
    elif sync:
        for i, (m, s) in enumerate(zip(mains, fc_segs)):
            src = facecam_start_us(m["source_timerange"]["start"], s["source_timerange"]["duration"], *sync)
            if i < args.from_clip and abs(src - s["source_timerange"]["start"]) / 1000 <= OUTLIER_MS:
                continue
            if src < 0 or src + s["source_timerange"]["duration"] > fc_dur_us:
                skipped += 1
                continue
            if abs(src - s["source_timerange"]["start"]) > 1000:
                s["source_timerange"]["start"] = src
                resynced.append(i)
    elif not aligned:
        raise SystemExit("❌ --place-only는 이미 본영상 컷에 맞춰진 얼굴캠 트랙에서만 쓸 수 있습니다")

    for s in fc_t["segments"]:
        clip = s.setdefault("clip", {})
        clip["scale"] = {"x": scale, "y": scale}
        clip["transform"] = {"x": tx, "y": ty}
    if choice in ("facecam", "screen"):
        main_vol, fc_vol = (0.0, 1.0) if choice == "facecam" else (1.0, 0.0)
        for segs, vol in ((d["tracks"][mi]["segments"], main_vol), (fc_t["segments"], fc_vol)):
            for s in segs:
                s["volume"] = vol
                if vol == 0.0:
                    s["last_nonzero_volume"] = 1.0
    if sync:
        print(f"   싱크 조정 {len(resynced)}개" + (f" (앞 {args.from_clip}개는 {OUTLIER_MS}ms 넘게 어긋난 것만)" if args.from_clip else "")
              + (f" | 얼굴캠 범위 밖이라 뺀 조각 {skipped}개" if skipped else ""))
    if args.dry_run:
        print("(dry-run — 쓰지 않음)")
        return

    print("\n💾 파일 저장 중...")
    write_4_files(proj, tl, d, write_root=root)

    # ── 검증: 다시 읽어서 ──
    d2 = json.loads(dp.read_text(encoding="utf-8"))
    ids = {m["id"] for lst in d2["materials"].values() if isinstance(lst, list) for m in lst if isinstance(m, dict) and "id" in m}
    m2 = sorted(d2["tracks"][mi]["segments"], key=start_of)
    f2 = sorted(next(t for t in d2["tracks"] if t["id"] == fc_t["id"])["segments"], key=start_of)
    problems = []
    if not skipped and (len(f2) != len(m2) or any(a["target_timerange"] != b["target_timerange"] for a, b in zip(f2, m2))):
        problems.append("얼굴캠이 본영상과 1:1로 안 맞음")
    if any(r not in ids for t in d2["tracks"] for s in t["segments"] for r in (s["material_id"], *s.get("extra_material_refs", []))):
        problems.append("끊긴 소재 참조")
    if any(abs(s["clip"]["transform"]["x"] - tx) > 1e-6 or abs(s["clip"]["scale"]["x"] - scale) > 1e-6 for s in f2):
        problems.append("위치·크기 미반영")
    if problems:
        print("❌ 검증 실패: " + " / ".join(problems) + " — 백업에서 되돌리세요")
        sys.exit(1)
    print(f"✓ 얼굴캠 {len(f2)}개 / 끊긴 참조 0 / 위치·크기 반영")

    if sync and resynced:
        by_start = {start_of(s): s for s in f2}
        x, sr = load_wav(audio_for(Path(vids[mains[0]["material_id"]]["path"])))
        long_first = sorted((i for i in resynced if start_of(mains[i]) in by_start), key=lambda i: -mains[i]["target_timerange"]["duration"])
        checks = sorted(long_first[:6], key=lambda i: start_of(mains[i]))[::2]   # 긴 조각 중 앞·중간·뒤
        with tempfile.TemporaryDirectory() as tmp:
            for i in checks:
                m, s = mains[i], by_start[start_of(mains[i])]
                ms = m["source_timerange"]["start"] / 1e6
                dur = min(m["source_timerange"]["duration"] / 1e6, 12)
                ref = envelope(extract_audio(fc_path, s["source_timerange"]["start"] / 1e6 - 0.1, dur + 0.2, sr, tmp), sr, 0.005)
                lag_ms = slide_match(ref, envelope(x[int(ms * sr): int((ms + dur) * sr)], sr, 0.005)) * 5 - 100
                t0 = start_of(m) / 1e6
                print(f"   싱크 실측 {int(t0 // 60):02d}:{t0 % 60:04.1f} ({dur:.0f}초): 어긋남 {lag_ms:+d}ms (한 프레임 ±17ms 이내면 정상)")


if __name__ == "__main__":
    main()
