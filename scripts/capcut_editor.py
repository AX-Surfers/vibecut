#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""
Vibecut — CapCut 자동 컷편집 스크립트 (의존성 없음, 순수 stdlib)

사용법:
  python3 capcut_editor.py <segments.json> [--project <프로젝트경로>]

segments.json 형식: [[start_sec, end_sec], ...] (원본 영상 기준)

예시:
  # 무음 제거만
  python3 capcut_editor.py /tmp/speech_segments.json

  # 무음+NG 모두 제거
  python3 capcut_editor.py /tmp/final_segments.json

  # 프로젝트 경로 지정
  python3 capcut_editor.py /tmp/final_segments.json \\
    --project ~/Movies/CapCut/User\\ Data/Projects/com.lveditor.draft/0526
"""

import argparse
import copy
import json
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

# _platform(플랫폼별 CapCut 종료 확인)은 main()에서만 필요하다. 모듈 최상단에서
# import하면 pick_video_track 같은 순수 함수를 다른 스크립트(subtitles_from_cuts.py)가
# 가져다 쓸 때도 _platform.py가 같은 폴더에 있어야 해서, 플러그인 캐시처럼
# 일부 파일만 복사된 환경에서 import 자체가 죽는다. → main() 안에서 지연 import.

# ────────────────────────────────────────────────
# 상수
# ────────────────────────────────────────────────

# CapCut은 30fps 프레임 단위로 타임스탬프를 정렬함
# 1프레임 = 1,000,000 / 30 = 33,333.333... µs
FPS = 30
FRAME_US = 1_000_000 / FPS  # 33333.333...


# ────────────────────────────────────────────────
# 유틸
# ────────────────────────────────────────────────

def new_id() -> str:
    return str(uuid.uuid4()).upper()


def snap_to_frame(us: float) -> int:
    """µs 값을 가장 가까운 30fps 프레임 번호로 변환 후 µs로 반환.
    정수 연산으로 부동소수점 오차를 방지.
    """
    frame = round(us * FPS / 1_000_000)   # 가장 가까운 프레임 번호 (정수)
    return frame_to_us(frame)


def frame_to_us(frame: int) -> int:
    """프레임 번호 → µs (정수 연산, 30fps 기준).
    frame * 1,000,000 / 30 을 반올림.
    예) frame 172 → 5,733,333 µs
    """
    numerator = frame * 1_000_000
    result = numerator // FPS
    if numerator % FPS * 2 >= FPS:
        result += 1
    return result


def list_timelines(project_dir: Path) -> tuple[list[dict], str | None]:
    """프로젝트의 타임라인 목록 [{id, name}]과 메인 타임라인 id.

    CapCut은 한 프로젝트에 타임라인을 여러 개 둘 수 있다(하단 탭 "타임라인 01/02").
    Timelines/project.json에 이름·id·메인 여부가 있고, 루트 draft_info.json은
    메인 타임라인의 사본이다. project.json이 없으면 폴더 목록으로 대체한다.
    """
    tl_dir = project_dir / "Timelines"
    if not tl_dir.is_dir():
        return [], None
    pj = tl_dir / "project.json"
    if pj.is_file():
        try:
            data = json.loads(pj.read_text(encoding="utf-8"))
            tls = [{"id": t["id"], "name": t.get("name", "")}
                   for t in data.get("timelines", []) if not t.get("is_marked_delete")]
            if tls:
                return tls, data.get("main_timeline_id")
        except (json.JSONDecodeError, KeyError):
            pass
    tls = [{"id": e.name, "name": ""} for e in sorted(tl_dir.iterdir())
           if e.is_dir() and "-" in e.name]
    return tls, (tls[0]["id"] if len(tls) == 1 else None)


def resolve_timeline(project_dir: Path, timeline: str | None = None) -> tuple[str | None, bool]:
    """(타임라인 uuid, 루트 draft_info.json도 같이 써야 하는지).

    - 구형 프로젝트(Timelines 없음): (None, True)
    - 타임라인 1개: 그것. 루트도 함께 갱신.
    - 여러 개: --timeline(이름 또는 id 앞부분)으로 골라야 한다. 지정이 없으면 목록을
      보여주고 중단한다 — 첫 폴더를 잡는 옛 동작은 엉뚱한 타임라인을 편집했다.
    - 루트 파일은 메인 타임라인의 사본이므로 메인일 때만 함께 쓴다.
    """
    tls, main_id = list_timelines(project_dir)
    if not tls:
        return None, True
    if timeline:
        key = timeline.replace(" ", "").lower()
        hit = [t for t in tls
               if t["name"].replace(" ", "").lower() == key or t["id"].lower().startswith(key)]
        if len(hit) != 1:
            names = ", ".join(f"{t['name'] or '(이름 없음)'} [{t['id'][:8]}]" for t in tls)
            raise SystemExit(f"❌ 타임라인 '{timeline}'을(를) 찾지 못했습니다. 후보: {names}")
        chosen = hit[0]
    elif len(tls) == 1:
        chosen = tls[0]
    else:
        names = "\n".join(f"   - {t['name'] or '(이름 없음)'}  [{t['id'][:8]}]"
                          + ("  ← 메인" if t["id"] == main_id else "") for t in tls)
        raise SystemExit(f"❌ 타임라인이 {len(tls)}개입니다. --timeline <이름>으로 지정하세요:\n{names}")
    is_main = main_id is None or chosen["id"] == main_id
    print(f"🗂  타임라인: {chosen['name'] or '(이름 없음)'} [{chosen['id'][:8]}]"
          + ("" if is_main else "  (메인 아님 → 루트 draft_info.json은 건드리지 않음)"))
    return chosen["id"], is_main


def draft_path_for(project_dir: Path, timeline_uuid: str | None) -> Path:
    if timeline_uuid:
        return project_dir / "Timelines" / timeline_uuid / "draft_info.json"
    return project_dir / "draft_info.json"


# ────────────────────────────────────────────────
# Materials 생성 함수
# ────────────────────────────────────────────────

def make_video_material(uid: str, orig_video: dict) -> dict:
    """원본 video material을 기반으로 새 material 생성 (id만 교체)"""
    m = copy.deepcopy(orig_video)
    m["id"] = uid
    return m


def make_speed(uid: str) -> dict:
    return {
        "id": uid,
        "type": "speed",
        "mode": 0,
        "speed": 1.0,
        "curve_speed": None
    }


def make_placeholder(uid: str) -> dict:
    return {
        "id": uid,
        "type": "placeholder_info",
        "meta_type": "none",
        "res_path": "",
        "res_text": "",
        "error_path": "",
        "error_text": ""
    }


def make_canvas(uid: str) -> dict:
    return {
        "id": uid,
        "type": "canvas_color",
        "color": "",
        "blur": 0.0,
        "image": "",
        "album_image": "",
        "image_id": "",
        "image_name": "",
        "source_platform": 0,
        "team_id": ""
    }


def make_sound_channel(uid: str) -> dict:
    return {
        "id": uid,
        "type": "none",
        "audio_channel_mapping": 0,
        "is_config_open": False
    }


def make_material_color(uid: str) -> dict:
    return {
        "id": uid,
        "is_color_clip": False,
        "is_gradient": False,
        "solid_color": "",
        "gradient_colors": [],
        "gradient_percents": [],
        "gradient_angle": 90.0,
        "width": 0.0,
        "height": 0.0
    }


def make_vocal_separation(uid: str) -> dict:
    return {
        "id": uid,
        "type": "vocal_separation",
        "choice": 0,
        "removed_sounds": [],
        "time_range": None,
        "production_path": "",
        "final_algorithm": "",
        "enter_from": ""
    }


def make_segment(seg_id: str, vid_id: str, extra_refs: list, source_start_us: int,
                 dur_us: int, timeline_pos_us: int, orig_segment: dict) -> dict:
    """원본 세그먼트 구조를 기반으로 새 세그먼트 생성"""
    s = copy.deepcopy(orig_segment)
    s["id"] = seg_id
    s["material_id"] = vid_id
    s["extra_material_refs"] = extra_refs
    s["source_timerange"] = {"start": source_start_us, "duration": dur_us}
    s["target_timerange"] = {"start": timeline_pos_us, "duration": dur_us}
    s["render_timerange"] = {"start": 0, "duration": 0}
    s["speed"] = 1.0
    s["volume"] = 1.0
    s["keyframe_refs"] = []
    s["common_keyframes"] = []
    s["caption_info"] = None
    s["render_index"] = 0
    return s


# ────────────────────────────────────────────────
# 핵심: 세그먼트 + materials 빌드
# ────────────────────────────────────────────────

def build_segments(final_segs: list, orig_video: dict, orig_segment: dict,
                   timeline_offset_frame: int = 0):
    """
    final_segs: [[start_sec, end_sec], ...] 원본 영상 기준
    orig_video:   draft['materials']['videos'][N]
    orig_segment: draft['tracks'][0]['segments'][N]
    timeline_offset_frame: 타임라인 시작 오프셋 (프레임 단위, 다중 영상 순차 배치 시 사용)

    반환: (segments_list, materials_dict, end_frame)
    """
    segments = []
    mat_videos = []
    mat_speeds = []
    mat_placeholders = []
    mat_canvases = []
    mat_sounds = []
    mat_colors = []
    mat_vocals = []

    # timeline_frame을 정수 프레임 단위로 누적
    # (µs로 누적하면 ±1µs 오차가 쌓여 target_timerange.start가 프레임 경계를 벗어남)
    timeline_frame = timeline_offset_frame
    for start, end in final_segs:
        # 30fps 프레임 번호로 변환 (정수)
        start_frame = round(start * FPS)
        end_frame   = round(end   * FPS)
        dur_frame   = end_frame - start_frame
        if dur_frame <= 0:
            continue  # 0프레임 구간 건너뜀
        if dur_frame < 13:
            continue  # CapCut 최소 클립 길이 미만 건너뜀 (~0.43초)

        # µs 변환: 프레임 번호 → 정수 µs
        start_us        = frame_to_us(start_frame)
        dur_us          = frame_to_us(end_frame) - frame_to_us(start_frame)
        timeline_pos_us = frame_to_us(timeline_frame)

        vid_id = new_id()
        spd_id = new_id()
        plc_id = new_id()
        cvs_id = new_id()
        snd_id = new_id()
        col_id = new_id()
        vcl_id = new_id()
        seg_id = new_id()

        mat_videos.append(make_video_material(vid_id, orig_video))
        mat_speeds.append(make_speed(spd_id))
        mat_placeholders.append(make_placeholder(plc_id))
        mat_canvases.append(make_canvas(cvs_id))
        mat_sounds.append(make_sound_channel(snd_id))
        mat_colors.append(make_material_color(col_id))
        mat_vocals.append(make_vocal_separation(vcl_id))

        extra_refs = [spd_id, plc_id, cvs_id, snd_id, col_id, vcl_id]
        segments.append(make_segment(
            seg_id, vid_id, extra_refs,
            start_us, dur_us, timeline_pos_us,
            orig_segment
        ))
        timeline_frame += dur_frame  # 프레임 단위 누적 (µs 오차 없음)

    materials = {
        "videos": mat_videos,
        "speeds": mat_speeds,
        "placeholder_infos": mat_placeholders,
        "canvases": mat_canvases,
        "sound_channel_mappings": mat_sounds,
        "material_colors": mat_colors,
        "vocal_separations": mat_vocals,
    }
    return segments, materials, timeline_frame


def clone_video_segment(tmpl_seg: dict, tmpl_video: dict, source_start_us: int, dur_us: int,
                        timeline_pos_us: int, materials: dict) -> dict:
    """기존 세그먼트를 새 구간으로 복제한다. 세그먼트마다 소재 7종을 새로 만들어 materials에 쌓고,
    위치·크기(clip)·볼륨·렌더 순서는 템플릿 값을 그대로 쓴다 (make_segment는 이 값들을 초기화한다).

    materials: build_segments가 돌려주는 것과 같은 키의 dict. 호출한 쪽이 draft에 이어붙인다.
    """
    vid, spd, plc, cvs, snd, col, vcl, sid = (new_id() for _ in range(8))
    for key, item in (("videos", make_video_material(vid, tmpl_video)), ("speeds", make_speed(spd)),
                      ("placeholder_infos", make_placeholder(plc)), ("canvases", make_canvas(cvs)),
                      ("sound_channel_mappings", make_sound_channel(snd)), ("material_colors", make_material_color(col)),
                      ("vocal_separations", make_vocal_separation(vcl))):
        materials.setdefault(key, []).append(item)
    seg = make_segment(sid, vid, [spd, plc, cvs, snd, col, vcl], source_start_us, dur_us, timeline_pos_us, tmpl_seg)
    for key in ("render_index", "track_render_index", "clip", "uniform_scale", "volume", "last_nonzero_volume"):
        if key in tmpl_seg:
            seg[key] = copy.deepcopy(tmpl_seg[key])
    return seg


# ────────────────────────────────────────────────
# 편집 대상 트랙 선택
# ────────────────────────────────────────────────

# 이미지 확장자 — 이런 소재를 참조하는 트랙은 편집 대상이 아니다
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".heic", ".tiff"}


def pick_video_track(draft: dict) -> int | None:
    """편집 대상 비디오 트랙의 인덱스를 고른다.

    ⚠ tracks[0] 고정 가정은 위험하다 (실전 실패 사례):
      사용자가 배경 이미지나 오버레이를 타임라인에 올리면 그것이 tracks[0]이
      되어, 원본 영상 대신 이미지를 잘라버린다. 실제로 배경 PNG 1장이
      "원본 180분 → 편집 후 7.8분, 38개 클립"으로 조각나고 원본 영상 트랙은
      그대로 남는 사고가 발생했다.

    실제 영상 파일(이미지가 아닌 것)을 참조하는 세그먼트가 가장 많은
    비디오 트랙을 고른다. 동점이면 세그먼트가 더 많은 쪽, 그다음 위쪽 트랙.
    """
    mats = {m["id"]: m for m in draft.get("materials", {}).get("videos", [])}
    cands = []
    for idx, track in enumerate(draft.get("tracks", [])):
        if track.get("type") != "video" or not track.get("segments"):
            continue
        n_video = 0
        for seg in track["segments"]:
            path = mats.get(seg.get("material_id"), {}).get("path", "")
            if path and Path(path).suffix.lower() not in IMAGE_EXTS:
                n_video += 1
        cands.append((n_video, len(track["segments"]), -idx, idx))
    if not cands:
        return None
    cands.sort(reverse=True)
    return cands[0][3]


# ────────────────────────────────────────────────
# draft_info.json 업데이트
# ────────────────────────────────────────────────

def update_draft(draft: dict, new_segments: list, new_materials: dict,
                 total_duration_us: int, track_idx: int = 0) -> dict:
    """draft dict에 새 세그먼트/materials를 적용하고 반환"""
    d = copy.deepcopy(draft)

    # 편집 대상 트랙의 세그먼트 교체
    d["tracks"][track_idx]["segments"] = new_segments

    # materials 교체 (7종)
    for key, val in new_materials.items():
        d["materials"][key] = val

    # 전체 재생 길이 갱신
    d["duration"] = total_duration_us

    return d


def write_4_files(project_dir: Path, timeline_uuid: str | None, updated_draft: dict,
                  write_root: bool = True):
    """4개 파일 모두 동일하게 저장 (수정 전 자동 백업).

    timeline_uuid가 None이면 루트 2개만, write_root=False(메인이 아닌 타임라인)면
    Timelines/<uuid>/ 2개만 저장한다 — 루트는 메인 타임라인의 사본이기 때문.
    """
    # 덮어쓰기 전 자동 백업
    try:
        from _lib_backup import backup_project_json
        backup_project_json(project_dir, tag="capcut_editor")
    except Exception as e:
        print(f"  ⚠ 백업 건너뜀: {e}")

    content = json.dumps(updated_draft, ensure_ascii=False, separators=(',', ':'))

    paths = []
    if write_root or not timeline_uuid:
        paths += [
            project_dir / "draft_info.json",
            project_dir / "draft_info.json.bak",
        ]
    if timeline_uuid:
        paths += [
            project_dir / "Timelines" / timeline_uuid / "draft_info.json",
            project_dir / "Timelines" / timeline_uuid / "draft_info.json.bak",
        ]

    for p in paths:
        p.write_text(content, encoding="utf-8")
        print(f"  ✅ {p}")

    # .locked 파일 삭제
    locked = project_dir / ".locked"
    if locked.exists():
        locked.unlink()
        print(f"  🗑️  {locked} 삭제됨")

    if timeline_uuid:
        timeline_locked = project_dir / "Timelines" / timeline_uuid / ".locked"
        if timeline_locked.exists():
            timeline_locked.unlink()
            print(f"  🗑️  {timeline_locked} 삭제됨")


# ────────────────────────────────────────────────
# 메인
# ────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="CapCut 자동 컷편집")
    parser.add_argument(
        "segments",
        nargs="+",
        help="편집 구간 JSON 파일 경로 [[start_sec, end_sec], ...] (여러 개 지정 시 순서대로 각 영상에 적용)"
    )
    parser.add_argument(
        "--project",
        required=True,
        help="CapCut 프로젝트 디렉토리 경로 "
             "(예: ~/Movies/CapCut/User Data/Projects/com.lveditor.draft/내프로젝트)"
    )
    parser.add_argument(
        "--no-check",
        action="store_true",
        help="CapCut 실행 여부 확인 건너뜀 (테스트용)"
    )
    parser.add_argument(
        "--timeline",
        default=None,
        help="타임라인이 여러 개인 프로젝트에서 편집할 타임라인 (이름 예: '타임라인 02', 또는 id 앞부분)"
    )
    parser.add_argument(
        "--track",
        type=int,
        default=None,
        help="편집할 비디오 트랙 인덱스 (기본: 실제 영상을 참조하는 트랙 자동 선택). "
             "배경 이미지/오버레이가 함께 올라간 프로젝트에서 대상을 강제할 때 사용"
    )
    args = parser.parse_args()

    # CapCut 실행 여부 확인
    if not args.no_check:
        from _platform import check_capcut_not_running
        check_capcut_not_running()

    project_dir = Path(args.project).expanduser()
    if not project_dir.exists():
        print(f"❌ 프로젝트 디렉토리가 없습니다: {project_dir}")
        sys.exit(1)

    print(f"📁 프로젝트: {project_dir.name}")
    timeline_uuid, write_root = resolve_timeline(project_dir, args.timeline)
    if not timeline_uuid:
        print("📂 구형 포맷 (Timelines 없음) — 루트 draft_info.json 사용")
    draft_path = draft_path_for(project_dir, timeline_uuid)

    with open(draft_path, encoding="utf-8") as f:
        draft = json.load(f)

    videos = draft["materials"]["videos"]
    mats_by_id = {m["id"]: m for m in videos}

    # 편집 대상 트랙 결정 (tracks[0] 고정 금지 — pick_video_track 독스트링 참고)
    if args.track is not None:
        track_idx = args.track
        if not (0 <= track_idx < len(draft["tracks"])):
            print(f"❌ --track {track_idx}: 트랙 인덱스 범위를 벗어났습니다 "
                  f"(0~{len(draft['tracks'])-1})")
            sys.exit(1)
    else:
        track_idx = pick_video_track(draft)
        if track_idx is None:
            print("❌ 편집할 비디오 트랙을 찾지 못했습니다.")
            sys.exit(1)

    video_tracks = [i for i, t in enumerate(draft["tracks"])
                    if t.get("type") == "video" and t.get("segments")]
    if len(video_tracks) > 1:
        print(f"⚠ 비디오 트랙이 {len(video_tracks)}개입니다 "
              f"(인덱스 {video_tracks}) → tracks[{track_idx}]를 편집 대상으로 선택")
        print("   다른 트랙을 편집하려면 --track N 으로 지정하세요.")

    orig_segments_list = draft["tracks"][track_idx]["segments"]

    seg_files = args.segments

    num = min(len(seg_files), len(videos), len(orig_segments_list))

    all_segs = []
    all_mats = {k: [] for k in ["videos", "speeds", "placeholder_infos", "canvases",
                                 "sound_channel_mappings", "material_colors", "vocal_separations"]}
    timeline_frame = 0

    for i in range(num):
        orig_segment = orig_segments_list[i]
        # 소재는 세그먼트가 실제로 참조하는 것을 쓴다.
        # videos[i]로 인덱싱하면 materials 순서와 트랙 순서가 어긋날 때 엉뚱한
        # 소재(예: 배경 이미지)를 편집하게 된다.
        orig_video = mats_by_id.get(orig_segment.get("material_id")) or videos[i]

        with open(seg_files[i], encoding="utf-8") as f:
            final_segs = json.load(f)

        name = orig_video["path"].split("/")[-1]
        ext = Path(orig_video["path"]).suffix.lower()
        if ext in IMAGE_EXTS:
            print(f"\n❌ 편집 대상이 이미지입니다: {name}")
            print("   원본 영상 트랙이 아닌 배경/오버레이 트랙을 잡았을 가능성이 큽니다.")
            print("   타임라인에서 이미지 트랙을 빼거나, --track N 으로 영상 트랙을 지정하세요.")
            sys.exit(1)

        total_sec = sum(e - s for s, e in final_segs)
        orig_min = orig_video["duration"] / 1_000_000 / 60
        cut_pct = (1 - total_sec / 60 / orig_min) * 100 if orig_min else 0
        print(f"\n🎬 영상 {i+1}: {name}")
        print(f"   원본 {orig_min:.1f}분 → 편집 후 {total_sec/60:.1f}분 "
              f"({cut_pct:.0f}% 감소, {len(final_segs)}개 클립)")
        if cut_pct > 90:
            print(f"   ⚠ 원본의 {cut_pct:.0f}%가 잘려나갑니다. 편집 대상이 맞는지 확인하세요.")

        new_segs, new_mats, timeline_frame = build_segments(
            final_segs, orig_video, orig_segment,
            timeline_offset_frame=timeline_frame
        )
        all_segs.extend(new_segs)
        for k in all_mats:
            all_mats[k].extend(new_mats.get(k, []))

    total_us = frame_to_us(timeline_frame)
    print(f"\n🔨 총 {len(all_segs)}개 세그먼트, 전체 {total_us/1e6/60:.1f}분")

    # draft 업데이트
    updated = update_draft(draft, all_segs, all_mats, total_us, track_idx=track_idx)

    # 4개 파일 저장
    print("\n💾 파일 저장 중...")
    write_4_files(project_dir, timeline_uuid, updated, write_root=write_root)

    print("\n✨ 완료! CapCut을 실행해서 확인하세요.")
    print(f"   프로젝트: {project_dir.name}")


if __name__ == "__main__":
    main()
