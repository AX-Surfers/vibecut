#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""
Vibecut — 사진 슬라이드쇼 CapCut 프로젝트 자동 생성

사용법:
  python3 scripts/photo_slideshow.py <사진_폴더> --audio <배경음악.m4a>
  python3 scripts/photo_slideshow.py <사진_폴더> --audio <배경음악.m4a> --pairs "001.jpeg,002.jpeg;003.jpeg,004.jpeg"
  python3 scripts/photo_slideshow.py <사진_폴더> --audio <배경음악.m4a> --srt <가사.srt>

옵션:
  --audio      배경음악 파일 (m4a/mp3/wav). 전체 슬라이드 길이가 음악 길이에 맞춰짐.
  --pairs      같은 컷 쌍 (좌우 분할 표시). "파일1,파일2;파일3,파일4" 형식.
  --srt        자막 SRT 파일 (가사 등). 지정 시 텍스트 트랙 자동 추가.
  --project-name  CapCut 프로젝트 이름 (기본: 폴더명)

CapCut 사진/영상 material 포맷 차이 (실측):
  필드                  영상(video)              사진(photo)
  ─────────────────────────────────────────────────────────
  type                  "video"                  "photo"
  duration              실제 길이(µs)             10_800_000_000 (3h 고정)
  has_audio             True/False               False
  extra_material_refs   7개 (loudness 포함)       6개 (loudness 없음)
  source_timerange      클립 구간                 표시 시간(=target_timerange)

주의:
  - 실행 전 CapCut을 완전히 종료해야 함
  - CapCut에 적어도 1개 프로젝트가 있어야 함 (템플릿용)
"""

import argparse
import copy
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

# ── 경로 상수 ──────────────────────────────────────────────────────────────────
CAPCUT_PROJECTS = Path.home() / "Movies/CapCut/User Data/Projects/com.lveditor.draft"
ROOT_META       = CAPCUT_PROJECTS / "root_meta_info.json"

FPS      = 30
FRAME_US = 1_000_000 // FPS

# 사진 포맷 상수 (CapCut 실측값)
PHOTO_DURATION_US = 10_800_000_000   # 3시간 고정값 — 표시 시간과 무관
PHOTO_EXTENSIONS  = {".jpg", ".jpeg", ".png", ".heic", ".webp", ".tiff", ".bmp"}
VIDEO_EXTENSIONS  = {".mp4", ".mov", ".m4v", ".avi", ".mkv"}


def new_id() -> str:
    return str(uuid.uuid4()).upper()


def snap_to_frame(seconds: float) -> int:
    frame = round(seconds * FPS)
    n = frame * 1_000_000
    r = n // FPS
    if n % FPS * 2 >= FPS:
        r += 1
    return r


def is_photo_file(path: Path) -> bool:
    return path.suffix.lower() in PHOTO_EXTENSIONS


def get_media_duration_us(path: Path) -> int:
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True
    )
    try:
        return int(float(r.stdout.strip()) * 1_000_000)
    except ValueError:
        return 0


def get_media_size(path: Path) -> tuple[int, int]:
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True
    )
    parts = r.stdout.strip().split(",")
    if len(parts) == 2:
        try:
            return int(parts[0]), int(parts[1])
        except ValueError:
            pass
    return 1920, 1080


def quit_capcut():
    r = subprocess.run(["pgrep", "-x", "CapCut"], capture_output=True)
    if r.returncode != 0:
        print("  CapCut 실행 중 아님")
        return
    print("  CapCut 종료 중...")
    subprocess.run(["pkill", "-x", "CapCut"])
    time.sleep(2)
    print("  CapCut 종료됨")


# ── 템플릿 프로젝트 자동 감지 ──────────────────────────────────────────────────
def find_template() -> Path:
    env = os.environ.get("VIBECUT_TEMPLATE_NAME")
    if env:
        return CAPCUT_PROJECTS / env
    for entry in sorted(CAPCUT_PROJECTS.iterdir()):
        if entry.is_dir() and (entry / "draft_info.json").exists():
            return entry
    raise FileNotFoundError("CapCut 프로젝트를 찾을 수 없습니다. 먼저 CapCut에서 프로젝트를 하나 만들어 주세요.")


# ── SRT 파싱 ────────────────────────────────────────────────────────────────────
def parse_srt(srt_path: Path) -> list[dict]:
    def ts(s):
        s = s.replace(",", ".")
        h, m, rest = s.split(":")
        return int(h) * 3600 + int(m) * 60 + float(rest)

    segs = []
    for block in srt_path.read_text(encoding="utf-8").strip().split("\n\n"):
        lines = block.strip().splitlines()
        if len(lines) < 3:
            continue
        t = lines[1].split(" --> ")
        text = " ".join(lines[2:]).strip()
        if text:
            segs.append({"start": ts(t[0].strip()), "end": ts(t[1].strip()), "text": text})
    return segs


# ── Material 헬퍼 ──────────────────────────────────────────────────────────────
def _make_speed():      return {"id": new_id(), "type": "speed",  "mode": 0, "speed": 1.0, "curve_speed": None}
def _make_placeholder():return {"id": new_id(), "type": "placeholder_info", "meta_type": "none",
                                "res_path": "", "res_text": "", "error_path": "", "error_text": ""}
def _make_canvas():     return {"id": new_id(), "type": "canvas_color", "color": "", "blur": 0.0,
                                "image": "", "album_image": "", "image_id": "", "image_name": "",
                                "source_platform": 0, "team_id": ""}
def _make_sound_ch():   return {"id": new_id(), "type": "none", "audio_channel_mapping": 0, "is_config_open": False}
def _make_mat_color():  return {"id": new_id(), "is_color_clip": False, "is_gradient": False,
                                "solid_color": "", "gradient_colors": [], "gradient_percents": [],
                                "gradient_angle": 90.0, "width": 0.0, "height": 0.0}
def _make_loudness():   return {"id": new_id(), "enable": False, "time_range": None,
                                "file_id": "", "target_loudness": 0.0, "loudness_param": None}
def _make_vocal_sep():  return {"id": new_id(), "type": "vocal_separation", "choice": 0,
                                "removed_sounds": [], "time_range": None, "production_path": "",
                                "final_algorithm": "", "enter_from": ""}


def build_media_material(orig_vid: dict, file_path: Path,
                         display_dur_us: int) -> tuple[dict, list[str]]:
    """
    사진/영상에 따라 올바른 material + extra_refs 생성.

    사진 (실측 기반):
      - type: "photo"
      - duration: PHOTO_DURATION_US (10_800_000_000, 3시간 고정)
      - has_audio: False
      - extra_refs: 6개 (loudness 없음)

    영상:
      - type: "video"
      - duration: 실제 길이
      - has_audio: True (오디오 포함 영상의 경우)
      - extra_refs: 7개 (loudness 포함)
    """
    sp  = _make_speed()
    ph  = _make_placeholder()
    cv  = _make_canvas()
    sc  = _make_sound_ch()
    mc  = _make_mat_color()
    vs  = _make_vocal_sep()

    w, h = get_media_size(file_path)
    mat = copy.deepcopy(orig_vid)
    mat["id"]            = new_id()
    mat["path"]          = str(file_path.resolve())
    mat["media_path"]    = ""
    mat["material_name"] = file_path.name
    mat["width"]         = w
    mat["height"]        = h

    if is_photo_file(file_path):
        mat["type"]      = "photo"
        mat["duration"]  = PHOTO_DURATION_US   # 3시간 고정
        mat["has_audio"] = False
        extra_refs = [sp["id"], ph["id"], cv["id"], sc["id"], mc["id"], vs["id"]]  # 6개
    else:
        video_dur = get_media_duration_us(file_path)
        mat["type"]      = "video"
        mat["duration"]  = video_dur
        mat["has_audio"] = True
        ld = _make_loudness()
        extra_refs = [sp["id"], ph["id"], cv["id"], sc["id"], mc["id"], ld["id"], vs["id"]]  # 7개

    extra_mats = [sp, ph, cv, sc, mc, vs] if is_photo_file(file_path) else [sp, ph, cv, sc, mc, ld, vs]
    return mat, extra_refs, extra_mats


def build_media_segment(orig_seg: dict, mat_id: str, extra_refs: list[str],
                        file_path: Path, tgt_start: int, tgt_dur: int,
                        tx: float = 0.0, ty: float = 0.0,
                        sx: float = 1.0, sy: float = 1.0,
                        render_idx: int = 0) -> dict:
    """타임라인 세그먼트 생성 (사진/영상 공통)."""
    seg = copy.deepcopy(orig_seg)
    src_dur = tgt_dur if is_photo_file(file_path) else get_media_duration_us(file_path)
    seg.update({
        "id":                  new_id(),
        "material_id":         mat_id,
        "extra_material_refs": extra_refs,
        "source_timerange":    {"start": 0, "duration": src_dur},
        "target_timerange":    {"start": tgt_start, "duration": tgt_dur},
        "speed":               1.0, "volume": 1.0,
        "render_index":        render_idx,
        "track_render_index":  render_idx,
        "clip": {
            "alpha": 1.0,
            "flip": {"horizontal": False, "vertical": False},
            "rotation": 0.0,
            "scale": {"x": sx, "y": sy},
            "transform": {"x": tx, "y": ty},
        },
        "uniform_scale": {"on": True, "value": 1.0},
    })
    return seg


# ── 프로젝트 생성 ────────────────────────────────────────────────────────────────
def create_slideshow(
    media_files: list[Path],
    pairs: set[frozenset],          # 같은 컷 쌍 {frozenset({파일A, 파일B}), ...}
    audio_path: Path | None,
    srt_segments: list[dict],
    project_name: str,
) -> Path:

    template_dir = find_template()
    print(f"  템플릿: {template_dir.name}")

    # ── 1. 템플릿 복사 + UUID 갱신 ──────────────────────────────────────────────
    project_dir = CAPCUT_PROJECTS / project_name
    if project_dir.exists():
        shutil.rmtree(project_dir)
    shutil.copytree(str(template_dir), str(project_dir))

    old_uuid = next(
        e.name for e in (project_dir / "Timelines").iterdir()
        if e.is_dir() and "-" in e.name
    )
    new_tl_uuid = new_id()
    old_tl = project_dir / "Timelines" / old_uuid
    new_tl = project_dir / "Timelines" / new_tl_uuid
    old_tl.rename(new_tl)

    # project.json 갱신
    now_us = int(time.time() * 1_000_000)
    proj_json = {
        "config": {"color_space": -1, "render_index_track_mode_on": False, "use_float_render": False},
        "create_time": now_us, "id": new_id(),
        "main_timeline_id": new_tl_uuid,
        "timelines": [{"create_time": now_us, "id": new_tl_uuid,
                       "is_marked_delete": False, "name": "타임라인 01", "update_time": now_us}],
        "update_time": now_us, "version": 0,
    }
    pj = json.dumps(proj_json, ensure_ascii=False, indent=2)
    (project_dir / "Timelines" / "project.json").write_text(pj, encoding="utf-8")
    (project_dir / "Timelines" / "project.json.bak").write_text(pj, encoding="utf-8")

    # ── 2. draft_info.json 로드 ──────────────────────────────────────────────────
    with open(template_dir / "draft_info.json", encoding="utf-8") as f:
        draft = json.load(f)

    project_id   = new_id()
    audio_dur_us = get_media_duration_us(audio_path) if audio_path else 0

    draft["id"]       = project_id
    draft["name"]     = project_name
    draft["duration"] = audio_dur_us
    draft["path"]     = ""

    orig_vid  = draft["materials"]["videos"][0]
    orig_vseg = draft["tracks"][0]["segments"][0]

    # ── 3. 슬라이드 타이밍 계산 ──────────────────────────────────────────────────
    # 쌍 파일 집합 (2번 나오면 안 됨)
    pair_seconds: dict[Path, Path] = {}
    for pair in pairs:
        a, b = list(pair)
        pair_seconds[b] = a   # b는 a의 오른쪽 (a가 먼저 나옴)

    ordered = [f for f in media_files if f not in pair_seconds]

    # 영상 파일은 실제 길이 사용, 나머지는 균등 분배
    fixed_dur   = sum(get_media_duration_us(f) for f in ordered if not is_photo_file(f))
    n_photo_slides = sum(1 for f in ordered if is_photo_file(f))
    if audio_dur_us > 0 and n_photo_slides > 0:
        slide_dur = (audio_dur_us - fixed_dur) // n_photo_slides
        slide_dur = (slide_dur // FRAME_US) * FRAME_US   # 프레임 정렬
    else:
        slide_dur = 5_000_000   # 기본 5초

    # ── 4. 세그먼트 & 재료 생성 ──────────────────────────────────────────────────
    all_videos   = []
    extra_speeds = []; extra_phs = []; extra_cvs = []; extra_scs = []
    extra_mcs    = []; extra_lds = []; extra_vss = []
    main_segs    = []
    overlay_segs = []  # 쌍의 오른쪽 사진

    cursor = 0
    for file_path in ordered:
        if is_photo_file(file_path):
            tgt_dur = slide_dur
        else:
            tgt_dur = get_media_duration_us(file_path)

        # 왼쪽 (메인 트랙)
        mat, extra_refs, extra_mats = build_media_material(orig_vid, file_path, tgt_dur)
        tx, ty, sx, sy = 0.0, 0.0, 1.0, 1.0

        # 같은 컷 쌍이면 좌우 분할
        pair_right = None
        for pair in pairs:
            if file_path in pair:
                other = [f for f in pair if f != file_path][0]
                if other in pair_seconds:  # other가 오른쪽
                    pair_right = other
                    break

        if pair_right:
            # 좌측 배치 (scale=0.5, transform.x=-0.5)
            tx, ty, sx, sy = -0.5, 0.0, 0.5, 0.5

        all_videos.append(mat)
        for m, lst in zip(extra_mats,
                          [extra_speeds, extra_phs, extra_cvs, extra_scs, extra_mcs,
                           extra_lds if len(extra_mats) == 7 else [], extra_vss]):
            lst.append(m)

        seg = build_media_segment(orig_vseg, mat["id"], extra_refs,
                                  file_path, cursor, tgt_dur, tx, ty, sx, sy, render_idx=0)
        main_segs.append(seg)

        # 오른쪽 사진 (overlay 트랙)
        if pair_right:
            mat_r, refs_r, mats_r = build_media_material(orig_vid, pair_right, tgt_dur)
            all_videos.append(mat_r)
            for m, lst in zip(mats_r,
                              [extra_speeds, extra_phs, extra_cvs, extra_scs, extra_mcs,
                               extra_lds if len(mats_r) == 7 else [], extra_vss]):
                lst.append(m)
            seg_r = build_media_segment(orig_vseg, mat_r["id"], refs_r,
                                        pair_right, cursor, tgt_dur,
                                        0.5, 0.0, 0.5, 0.5, render_idx=1)
            overlay_segs.append(seg_r)

        cursor += tgt_dur

    # ── 5. 오디오 트랙 ───────────────────────────────────────────────────────────
    audio_track = None
    audio_mat   = None
    if audio_path:
        audio_mat = {
            "id": new_id(), "type": "music", "name": audio_path.name,
            "path": str(audio_path.resolve()), "duration": audio_dur_us,
            "album_image_path": "", "check_flag": 1,
            "clip_id": "", "effect_id": "", "formula_id": "", "genre": "",
            "item_id": "", "music_id": "", "request_id": "", "search_id": "",
            "sub_type": "none", "text": "", "tone_type": "none",
            "genre_nums": 0, "is_ai_clone_tone": False, "is_copyright": False,
            "is_text_read": False, "vocal_separation_choice": 0,
            "local_material_id": "", "beats": None,
        }
        audio_seg = {
            "id": new_id(), "material_id": audio_mat["id"],
            "source_timerange": {"start": 0, "duration": audio_dur_us},
            "target_timerange": {"start": 0, "duration": audio_dur_us},
            "render_timerange": {"start": 0, "duration": 0},
            "desc": "", "state": 0, "speed": 1.0, "volume": 1.0,
            "last_nonzero_volume": 1.0, "is_loop": False, "render_index": 0,
            "reverse": False, "intensifies_audio": False, "cartoon": False,
            "is_tone_modify": False,
            "clip": {"scale": {"x": 1.0, "y": 1.0}, "rotation": 0.0,
                     "transform": {"x": 0.0, "y": 0.0},
                     "flip": {"vertical": False, "horizontal": False}, "alpha": 1.0},
            "uniform_scale": {"on": True, "value": 1.0},
            "extra_material_refs": [], "keyframe_refs": [],
            "track_render_index": 0, "track_attribute": 0,
        }
        audio_track = {"id": new_id(), "type": "audio", "flag": 0, "attribute": 0,
                       "name": "", "is_default_name": True, "segments": [audio_seg]}

    # ── 6. 자막 트랙 (SRT 제공 시) ───────────────────────────────────────────────
    text_track  = None
    text_mats   = []
    if srt_segments:
        orig_text = draft["materials"].get("texts", [None])[0]
        orig_tseg = next((t for t in draft["tracks"] if t["type"] == "text"), None)
        orig_tseg = orig_tseg["segments"][0] if orig_tseg else None

        if orig_text and orig_tseg:
            text_segs = []
            for i, seg in enumerate(srt_segments):
                text  = seg["text"]
                s_us  = snap_to_frame(seg["start"])
                e_us  = snap_to_frame(seg["end"])
                dur   = e_us - s_us
                if dur <= 0:
                    continue

                mat_id = new_id()
                new_text = copy.deepcopy(orig_text)
                new_text["id"] = mat_id
                c = json.loads(orig_text["content"])
                c["text"] = text
                for style in c.get("styles", []):
                    style["range"] = [0, len(text)]
                new_text["content"] = json.dumps(c, ensure_ascii=False)
                # 검은 외곽선
                new_text["border_color"]  = "#000000"
                new_text["border_width"]  = 0.15
                new_text["border_alpha"]  = 1.0
                stroke = {"content": {"render_type": "solid",
                                      "solid": {"alpha": 1.0, "color": [0, 0, 0]}},
                          "width": 0.15}
                c2 = json.loads(new_text["content"])
                for st in c2.get("styles", []):
                    st["strokes"] = [stroke]
                new_text["content"] = json.dumps(c2, ensure_ascii=False)
                text_mats.append(new_text)

                new_tseg = copy.deepcopy(orig_tseg)
                new_tseg.update({"id": new_id(), "material_id": mat_id,
                                  "source_timerange": None,
                                  "target_timerange": {"start": s_us, "duration": dur},
                                  "render_index": 14000 + i})
                text_segs.append(new_tseg)

            orig_ttrack = next((t for t in draft["tracks"] if t["type"] == "text"), None)
            if orig_ttrack:
                text_track = copy.deepcopy(orig_ttrack)
                text_track["id"]       = new_id()
                text_track["segments"] = text_segs

    # ── 7. draft 조립 ────────────────────────────────────────────────────────────
    draft["materials"]["videos"]                 = all_videos
    draft["materials"]["speeds"]                 = extra_speeds
    draft["materials"]["placeholder_infos"]      = extra_phs
    draft["materials"]["canvases"]               = extra_cvs
    draft["materials"]["sound_channel_mappings"] = extra_scs
    draft["materials"]["material_colors"]        = extra_mcs
    draft["materials"]["loudnesses"]             = [l for l in extra_lds if l]
    draft["materials"]["vocal_separations"]      = extra_vss
    draft["materials"]["audios"]                 = [audio_mat] if audio_mat else []
    draft["materials"]["texts"]                  = text_mats

    tracks = [{"id": new_id(), "type": "video", "flag": 0, "attribute": 0,
               "name": "", "is_default_name": True, "segments": main_segs}]
    if overlay_segs:
        tracks.append({"id": new_id(), "type": "video", "flag": 0, "attribute": 0,
                       "name": "", "is_default_name": True, "segments": overlay_segs})
    if audio_track:
        tracks.append(audio_track)
    if text_track:
        tracks.append(text_track)
    draft["tracks"] = tracks

    # ── 8. 4개 파일 저장 ──────────────────────────────────────────────────────────
    draft_str = json.dumps(draft, ensure_ascii=False, separators=(",", ":"))
    for p in [project_dir / "draft_info.json",
              project_dir / "draft_info.json.bak",
              new_tl / "draft_info.json",
              new_tl / "draft_info.json.bak"]:
        p.write_text(draft_str, encoding="utf-8")

    # 불필요한 파일 삭제
    for fname in ["attachment_editing.json", "attachment_pc_common.json",
                  "draft.extra", "draft_cover.jpg", "key_value.json",
                  "performance_opt_info.json", "template.tmp"]:
        for base in [project_dir, new_tl]:
            p = base / fname
            if p.exists():
                p.unlink()

    locked = project_dir / ".locked"
    if locked.exists():
        locked.unlink()

    return project_dir, project_id, audio_dur_us


def register_project(project_dir: Path, project_id: str, project_name: str,
                     duration_us: int, media_path: Path):
    """root_meta_info.json + draft_meta_info.json 등록"""
    now_us  = int(time.time() * 1_000_000)
    now_sec = int(time.time())

    with open(ROOT_META, encoding="utf-8") as f:
        root = json.load(f)

    template_entry = root["all_draft_store"][0]
    root["all_draft_store"] = [
        d for d in root["all_draft_store"]
        if project_name not in d.get("draft_fold_path", "")
    ]
    entry = copy.deepcopy(template_entry)
    entry.update({
        "draft_id":          project_id,
        "draft_name":        project_name,
        "draft_fold_path":   str(project_dir),
        "draft_json_file":   str(project_dir / "draft_info.json"),
        "draft_cover":       "draft_cover.jpg",
        "tm_draft_create":   now_us,
        "tm_draft_modified": now_us,
        "tm_duration":       duration_us,
    })
    root["all_draft_store"].insert(0, entry)
    with open(ROOT_META, "w", encoding="utf-8") as f:
        json.dump(root, f, ensure_ascii=False, indent=2)

    (project_dir / "draft_meta_info.json").write_text(
        json.dumps({
            "draft_fold_path": str(project_dir),
            "draft_id":        project_id,
            "draft_name":      project_name,
            "tm_draft_create": now_us,
            "tm_duration":     duration_us,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def main():
    parser = argparse.ArgumentParser(description="사진 슬라이드쇼 CapCut 프로젝트 생성")
    parser.add_argument("folder",         help="사진/영상 폴더")
    parser.add_argument("--audio",        default=None, help="배경음악 파일")
    parser.add_argument("--pairs",        default=None,
                        help='같은 컷 쌍. "파일A,파일B;파일C,파일D" 형식')
    parser.add_argument("--srt",          default=None, help="자막 SRT 파일")
    parser.add_argument("--project-name", default=None, help="CapCut 프로젝트 이름")
    args = parser.parse_args()

    folder = Path(args.folder).resolve()
    if not folder.is_dir():
        print(f"오류: 폴더를 찾을 수 없습니다 — {folder}", file=sys.stderr)
        sys.exit(1)

    # 미디어 파일 정렬
    exts = PHOTO_EXTENSIONS | VIDEO_EXTENSIONS
    media_files = sorted(f for f in folder.iterdir()
                         if f.suffix.lower() in exts and not f.name.startswith("."))
    print(f"미디어 파일: {len(media_files)}개")

    # 쌍 파싱
    pairs: set[frozenset] = set()
    if args.pairs:
        for pair_str in args.pairs.split(";"):
            parts = [folder / p.strip() for p in pair_str.split(",")]
            if len(parts) == 2:
                pairs.add(frozenset(parts))

    # 오디오
    audio_path = Path(args.audio).resolve() if args.audio else None

    # SRT
    srt_segments = []
    if args.srt:
        srt_segments = parse_srt(Path(args.srt).resolve())
        print(f"자막: {len(srt_segments)}개")

    project_name = args.project_name or folder.name

    print("\n[1/3] CapCut 종료")
    quit_capcut()

    print(f"\n[2/3] 프로젝트 생성: {project_name}")
    project_dir, project_id, duration_us = create_slideshow(
        media_files, pairs, audio_path, srt_segments, project_name
    )

    print("\n[3/3] 등록")
    register_project(project_dir, project_id, project_name, duration_us,
                     media_files[0] if media_files else folder)

    print(f"\n✓ 완료: {project_dir.name}")
    print(f"  슬라이드: {len(media_files)}개  |  쌍: {len(pairs)}개  |  자막: {len(srt_segments)}개")
    print(f"  → CapCut을 열어 '{project_name}' 프로젝트를 확인하세요.")


if __name__ == "__main__":
    main()
