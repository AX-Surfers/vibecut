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
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

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


def find_timeline_uuid(project_dir: Path) -> str:
    """Timelines/ 하위 첫 번째 UUID 디렉토리 반환"""
    timelines_dir = project_dir / "Timelines"
    for entry in timelines_dir.iterdir():
        if entry.is_dir() and "-" in entry.name:
            return entry.name
    raise FileNotFoundError(f"Timelines UUID 디렉토리를 찾을 수 없음: {timelines_dir}")


def check_capcut_not_running():
    """CapCut 실행 중이면 경고 후 종료"""
    result = subprocess.run(
        ["pgrep", "-i", "capcut"],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        print("❌ CapCut이 실행 중입니다. 완전히 종료 후 다시 실행하세요.")
        print("   Cmd+Q 로 종료 후: ps aux | grep -i capcut | grep -v grep")
        sys.exit(1)


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

def build_segments(final_segs: list, orig_video: dict, orig_segment: dict):
    """
    final_segs: [[start_sec, end_sec], ...] 원본 영상 기준
    orig_video:   draft['materials']['videos'][0]
    orig_segment: draft['tracks'][0]['segments'][0]

    반환: (segments_list, materials_dict)
    """
    US = 1_000_000
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
    timeline_frame = 0
    for start, end in final_segs:
        # 30fps 프레임 번호로 변환 (정수)
        start_frame = round(start * FPS)
        end_frame   = round(end   * FPS)
        dur_frame   = end_frame - start_frame
        if dur_frame <= 0:
            continue  # 0프레임 구간 건너뜀

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

    total_duration_us = frame_to_us(timeline_frame)
    materials = {
        "videos": mat_videos,
        "speeds": mat_speeds,
        "placeholder_infos": mat_placeholders,
        "canvases": mat_canvases,
        "sound_channel_mappings": mat_sounds,
        "material_colors": mat_colors,
        "vocal_separations": mat_vocals,
    }
    return segments, materials, total_duration_us


# ────────────────────────────────────────────────
# draft_info.json 업데이트
# ────────────────────────────────────────────────

def update_draft(draft: dict, new_segments: list, new_materials: dict,
                 total_duration_us: int) -> dict:
    """draft dict에 새 세그먼트/materials를 적용하고 반환"""
    d = copy.deepcopy(draft)

    # tracks[0] 세그먼트 교체
    d["tracks"][0]["segments"] = new_segments

    # materials 교체 (7종)
    for key, val in new_materials.items():
        d["materials"][key] = val

    # 전체 재생 길이 갱신
    d["duration"] = total_duration_us

    return d


def write_4_files(project_dir: Path, timeline_uuid: str, updated_draft: dict):
    """4개 파일 모두 동일하게 저장"""
    content = json.dumps(updated_draft, ensure_ascii=False, separators=(',', ':'))

    paths = [
        project_dir / "draft_info.json",
        project_dir / "draft_info.json.bak",
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

    timeline_locked = project_dir / "Timelines" / timeline_uuid / ".locked"
    if timeline_locked.exists():
        timeline_locked.unlink()
        print(f"  🗑️  {timeline_locked} 삭제됨")


# ────────────────────────────────────────────────
# 메인
# ────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="CapCut 자동 컷편집")
    parser.add_argument("segments", help="편집 구간 JSON 파일 경로 [[start_sec, end_sec], ...]")
    parser.add_argument(
        "--project",
        default=os.path.expanduser(
            "~/Movies/CapCut/User Data/Projects/com.lveditor.draft/0526"
        ),
        help="CapCut 프로젝트 디렉토리 경로"
    )
    parser.add_argument(
        "--no-check",
        action="store_true",
        help="CapCut 실행 여부 확인 건너뜀 (테스트용)"
    )
    args = parser.parse_args()

    # CapCut 실행 여부 확인
    if not args.no_check:
        check_capcut_not_running()

    project_dir = Path(args.project).expanduser()
    if not project_dir.exists():
        print(f"❌ 프로젝트 디렉토리가 없습니다: {project_dir}")
        sys.exit(1)

    # Timeline UUID 찾기
    timeline_uuid = find_timeline_uuid(project_dir)
    print(f"📁 프로젝트: {project_dir.name}")
    print(f"🆔 Timeline UUID: {timeline_uuid}")

    # 원본 draft 로드 (Timelines 파일 기준)
    timeline_draft_path = project_dir / "Timelines" / timeline_uuid / "draft_info.json"
    with open(timeline_draft_path, encoding="utf-8") as f:
        draft = json.load(f)

    orig_video = draft["materials"]["videos"][0]
    orig_segment = draft["tracks"][0]["segments"][0]
    print(f"🎬 원본 영상: {orig_video['path']}")
    print(f"⏱️  원본 길이: {orig_video['duration'] / 1_000_000 / 60:.1f}분")

    # 편집 구간 로드
    with open(args.segments, encoding="utf-8") as f:
        final_segs = json.load(f)
    print(f"\n✂️  편집 구간: {len(final_segs)}개")
    total_sec = sum(e - s for s, e in final_segs)
    print(f"📊 편집 후 길이: {total_sec / 60:.1f}분 ({total_sec:.0f}초)")
    orig_min = orig_video["duration"] / 1_000_000 / 60
    print(f"📉 단축률: {(1 - total_sec/60/orig_min)*100:.0f}% 감소")

    # 세그먼트 + materials 빌드
    print("\n🔨 세그먼트 생성 중...")
    new_segments, new_materials, total_us = build_segments(
        final_segs, orig_video, orig_segment
    )
    print(f"  세그먼트: {len(new_segments)}개")
    print(f"  videos:   {len(new_materials['videos'])}개")

    # draft 업데이트
    updated = update_draft(draft, new_segments, new_materials, total_us)

    # 4개 파일 저장
    print("\n💾 파일 저장 중...")
    write_4_files(project_dir, timeline_uuid, updated)

    print("\n✨ 완료! CapCut을 실행해서 확인하세요.")
    print(f"   프로젝트: {project_dir.name}")


if __name__ == "__main__":
    main()
