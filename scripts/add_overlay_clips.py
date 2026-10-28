#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""CapCut 타임라인 위에 알파 채널 영상(Remotion 모션그래픽 등)을 오버레이 클립으로 얹는다.

- 기존 트랙(비디오·오디오·자막)은 그대로 두고, 비디오 트랙들 바로 위·자막 트랙 아래에 새 트랙을 만든다.
  (자막이 모션그래픽에 가려지지 않는다.)
- 클립끼리 시간이 겹치면 겹치지 않는 트랙(레인)으로 나눠 담는다.
- 이전에 이 도구로 넣은 오버레이 트랙(이름 'motion')은 지우고 다시 넣는다 — 같은 명령을 다시 돌려도 중복되지 않는다.
- 위치·크기는 전체 화면(1920×1080 클립 그대로)이다. 카드 배치는 렌더 쪽에서 정한다.

사용:
  uv run add_overlay_clips.py --project <CapCut 프로젝트> [--timeline "타임라인 01"] \\
      --manifest clips.json [--dry-run]

clips.json: [{"file": "/절대/경로/c01.mov", "start": 0.0}, ...]   (start = 타임라인 시각(초))
"""
import argparse
import copy
import json
import subprocess
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

TRACK_NAME = "motion"


def start_of(s: dict) -> int:
    return s["target_timerange"]["start"]


def end_of(s: dict) -> int:
    return start_of(s) + s["target_timerange"]["duration"]


def probe(path: str) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height,nb_frames,r_frame_rate:format=duration",
         "-of", "json", path], capture_output=True, text=True, check=True).stdout
    j = json.loads(out)
    st = j["streams"][0]
    return {"width": st["width"], "height": st["height"], "duration": float(j["format"]["duration"])}


def main():
    ap = argparse.ArgumentParser(description="CapCut에 알파 영상 오버레이 클립 추가")
    ap.add_argument("--project", required=True)
    ap.add_argument("--timeline", default=None)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    proj = Path(args.project).expanduser()
    tl, root = resolve_timeline(proj, args.timeline)
    dp = draft_path_for(proj, tl)
    if not args.dry_run:
        check_capcut_not_running()
    d = json.loads(dp.read_text(encoding="utf-8"))
    M = d["materials"]

    clips = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    infos = []
    for c in clips:
        p = Path(c["file"])
        if not p.exists():
            raise SystemExit(f"❌ 파일이 없습니다: {p}")
        info = probe(str(p))
        info.update(file=str(p), start=frame_to_us(round(c["start"] * FPS)))
        info["frames"] = round(info["duration"] * FPS)
        infos.append(info)
    infos.sort(key=lambda i: i["start"])

    # 이전 실행에서 넣은 motion 트랙 제거
    old = [t for t in d["tracks"] if t["type"] == "video" and t.get("name") == TRACK_NAME]
    old_mats = {r for t in old for s in t["segments"] for r in (s["material_id"], *s.get("extra_material_refs", []))}
    d["tracks"] = [t for t in d["tracks"] if t not in old]

    videos = [t for t in d["tracks"] if t["type"] == "video"]
    if not videos:
        raise SystemExit("❌ 비디오 트랙이 없습니다")
    tmpl_track = videos[-1]
    tmpl_seg = sorted(tmpl_track["segments"], key=start_of)[0]
    vids = {m["id"]: m for m in M.get("videos", [])}
    tmpl_vid = vids[tmpl_seg["material_id"]]

    # 겹치지 않는 레인에 나눠 담는다
    lanes: list[list[dict]] = []
    for info in infos:
        end = info["start"] + frame_to_us(info["frames"])
        for lane in lanes:
            if lane[-1]["_end"] <= info["start"]:
                lane.append({**info, "_end": end})
                break
        else:
            lanes.append([{**info, "_end": end}])
    print(f"클립 {len(infos)}개 → 트랙 {len(lanes)}개")
    for i in infos:
        print(f"   {i['start'] / 1e6:8.2f}s  {i['frames'] / FPS:5.1f}초  {Path(i['file']).name}")
    if args.dry_run:
        print("(dry-run — 쓰지 않음)")
        return

    added: dict = {}
    new_tracks = []
    for li, lane in enumerate(lanes):
        segs = []
        for info in lane:
            vm = copy.deepcopy(tmpl_vid)
            vm.update({"path": info["file"], "material_name": Path(info["file"]).name, "width": info["width"],
                       "height": info["height"], "duration": frame_to_us(info["frames"]), "has_audio": False,
                       "unique_id": new_id().replace("-", "").lower()})
            seg = clone_video_segment(tmpl_seg, vm, 0, frame_to_us(info["frames"]), info["start"], added, draft_materials=M)
            seg["volume"] = 0.0
            seg["track_render_index"] = len(videos) + li
            seg["render_index"] = 0
            seg["clip"] = {"alpha": 1.0, "flip": {"horizontal": False, "vertical": False}, "rotation": 0.0,
                           "scale": {"x": 1.0, "y": 1.0}, "transform": {"x": 0.0, "y": 0.0}}
            segs.append(seg)
        new_tracks.append({"id": new_id(), "attribute": 0, "flag": tmpl_track.get("flag", 0), "is_default_name": False,
                           "name": TRACK_NAME, "type": "video", "segments": segs})

    # 비디오 트랙들 바로 위(첫 텍스트 트랙 앞)에 끼운다
    at = max(i for i, t in enumerate(d["tracks"]) if t["type"] == "video") + 1
    d["tracks"][at:at] = new_tracks

    used = {r for t in d["tracks"] for s in t["segments"] for r in (s.get("material_id"), *s.get("extra_material_refs", [])) if r}
    for key, lst in M.items():
        if isinstance(lst, list):
            M[key] = [m for m in lst if not (isinstance(m, dict) and m.get("id") in old_mats - used)]
    for key, lst in added.items():
        M.setdefault(key, []).extend(lst)
    d["duration"] = max(end_of(s) for t in d["tracks"] for s in t["segments"])

    print("\n💾 파일 저장 중...")
    write_4_files(proj, tl, d, write_root=root)

    # 검증: 다시 읽어서
    d2 = json.loads(dp.read_text(encoding="utf-8"))
    ids = {m["id"] for lst in d2["materials"].values() if isinstance(lst, list) for m in lst if isinstance(m, dict) and "id" in m}
    problems = []
    for t in d2["tracks"]:
        ts = sorted(t["segments"], key=start_of)
        if any(end_of(a) > start_of(b) + 1 for a, b in zip(ts, ts[1:])):
            problems.append(f"{t['type']} 트랙 겹침")
        if any(r not in ids for s in ts for r in (s.get("material_id"), *s.get("extra_material_refs", [])) if r):
            problems.append(f"{t['type']} 트랙에 끊긴 소재 참조")
    mt = [t for t in d2["tracks"] if t.get("name") == TRACK_NAME]
    if sum(len(t["segments"]) for t in mt) != len(infos):
        problems.append("오버레이 클립 수가 다름")
    if problems:
        print("❌ 검증 실패: " + " / ".join(sorted(set(problems))) + " — 백업에서 되돌리세요")
        sys.exit(1)
    print(f"✓ 오버레이 클립 {len(infos)}개 / 트랙 {len(mt)}개 / 끊긴 참조 0 / 겹침 0 / 전체 {d2['duration'] / 1e6 / 60:.2f}분")


if __name__ == "__main__":
    main()
