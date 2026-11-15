#!/usr/bin/env python3
"""본영상 클립 중 원본 시각 범위에 드는 것들을 화면 한 점 중심으로 확대한다 (렌더 없음, JSON만 수정).

claude meeting 실전: "1분대에 클로드가 참가 신청하는 부분 확대" → 오른쪽 People 패널 중심 (1577, 256)을 2.8배.
- 좌표(cx, cy)는 원본 영상 픽셀 (왼쪽 위가 0,0). 먼저 ffmpeg로 그 시각 프레임을 원본 해상도로 잘라
  (crop=W/scale:H/scale:cx-W/scale/2:cy-H/scale/2) 미리보기로 영역을 확인한다.
  축소 미리보기에서 읽은 좌표를 그대로 쓰면 엉뚱한 곳이 확대된다 (실제로 한 번 틀림).
- CapCut transform 단위: 캔버스 절반 = 1, y는 위가 +. 확대한 화면이 캔버스 밖으로 비면 거부한다.
- 확대는 클립 단위로 한 번에 바뀐다. 서서히 확대(키프레임)는 지원하지 않는다.

사용:
  uv run zoom_clips.py --project <CapCut 프로젝트> [--timeline "타임라인 01"] \\
      --src-start 197.5 --src-end 210.2 --scale 2.8 --cx 1577 --cy 256 [--dry-run]
  되돌리기: --scale 1 --cx <W/2> --cy <H/2>
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _platform import check_capcut_not_running  # noqa: E402
from capcut_editor import draft_path_for, pick_video_track, resolve_timeline, write_4_files  # noqa: E402


def zoom_transform(cx: float, cy: float, scale: float, w: int, h: int) -> tuple[float, float]:
    """원본 픽셀 (cx, cy)가 화면 가운데 오도록 하는 CapCut transform (캔버스 절반=1, 위가 +)."""
    tx, ty = -(cx - w / 2) * scale / (w / 2), (cy - h / 2) * scale / (h / 2)
    if abs(tx) > scale - 1 + 1e-9 or abs(ty) > scale - 1 + 1e-9:
        raise ValueError("확대 영역이 화면 밖으로 나갑니다 — 중심을 안쪽으로 옮기거나 배율을 키우세요")
    return tx, ty


def main():
    ap = argparse.ArgumentParser(description="본영상 클립을 한 점 중심으로 확대")
    ap.add_argument("--project", required=True, help="CapCut 프로젝트 디렉토리")
    ap.add_argument("--timeline", default=None, help="타임라인이 여러 개면 이름 또는 id 앞부분")
    ap.add_argument("--src-start", type=float, required=True, help="원본 초 — 클립 시작이 이 범위 안이면 확대")
    ap.add_argument("--src-end", type=float, required=True)
    ap.add_argument("--scale", type=float, required=True, help="배율 (1 = 원래대로)")
    ap.add_argument("--cx", type=float, required=True, help="확대 중심 x (원본 픽셀)")
    ap.add_argument("--cy", type=float, required=True, help="확대 중심 y (원본 픽셀, 위가 0)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    proj = Path(args.project).expanduser()
    tl, root = resolve_timeline(proj, args.timeline)
    if not args.dry_run:
        check_capcut_not_running()
    d = json.loads(draft_path_for(proj, tl).read_text(encoding="utf-8"))
    w, h = d["canvas_config"]["width"], d["canvas_config"]["height"]
    try:
        tx, ty = zoom_transform(args.cx, args.cy, args.scale, w, h)
    except ValueError as e:
        raise SystemExit(f"❌ {e}") from None
    hits = []
    for s in d["tracks"][pick_video_track(d)]["segments"]:
        st = s["source_timerange"]["start"] / 1e6
        if args.src_start - 0.05 <= st < args.src_end:
            s["clip"]["scale"] = {"x": args.scale, "y": args.scale}
            s["clip"]["transform"] = {"x": tx, "y": ty}
            hits.append(s["target_timerange"]["start"] / 1e6)
    if not hits:
        raise SystemExit("❌ 그 원본 범위에서 시작하는 본영상 클립이 없습니다")
    print(f"🔍 클립 {len(hits)}개 (타임라인 {min(hits):.2f}초~) {args.scale}배, transform=({tx:.4f}, {ty:.4f})")
    if args.dry_run:
        print("(dry-run — 쓰지 않음)")
        return
    write_4_files(proj, tl, d, write_root=root)


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        assert zoom_transform(960, 540, 2, 1920, 1080) == (0.0, 0.0)
        tx, ty = zoom_transform(1577, 256, 2.8, 1920, 1080)
        assert abs(tx + 1.7996) < 1e-3 and abs(ty + 1.4726) < 1e-3, (tx, ty)   # 오른쪽 위 → 왼쪽·아래로 옮김
        try:
            zoom_transform(1900, 256, 2.8, 1920, 1080)
            raise AssertionError("화면 밖인데 통과함")
        except ValueError:
            pass
        print("✓ zoom_clips OK")
    else:
        main()
