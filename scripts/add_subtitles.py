#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "faster-whisper>=1.0.0",
# ]
# ///
"""
Vibecut — 영상 → Whisper 전사 → CapCut 새 프로젝트에 자막 트랙 추가

사용법:
  uv run scripts/add_subtitles.py <video.mov>
  uv run scripts/add_subtitles.py <video.mov> --project-name <이름>
  uv run scripts/add_subtitles.py <video.mov> --srt <기존.srt>  # Whisper 생략
  uv run scripts/add_subtitles.py <video.mov> --model medium    # 모델 선택
  uv run scripts/add_subtitles.py <video.mov> --splits /tmp/subtitle_splits.json  # AI 분할 적용

환경변수 (선택):
  VIBECUT_TEMPLATE_NAME  — CapCut 템플릿 프로젝트 이름 (기본: 자동 감지)
  VIBECUT_CORRECTIONS    — 오인식 사전 경로 (기본: data/corrections.json)

주의:
  - 실행 전 CapCut을 완전히 종료해야 함 (스크립트가 자동 종료함)
  - 사용자의 CapCut에 적어도 1개 프로젝트가 있어야 함 (템플릿용)
"""

import argparse
import copy
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

# ──────────────────────────────────────────
# 경로 상수
# ──────────────────────────────────────────
CAPCUT_PROJECTS = Path.home() / "Movies/CapCut/User Data/Projects/com.lveditor.draft"
ROOT_META       = CAPCUT_PROJECTS / "root_meta_info.json"

# 템플릿 프로젝트: 환경변수 → 자동 감지 (CapCut 내 첫 프로젝트)
# 반드시 실제로 열리는 프로젝트여야 함 (이 폴더를 통째로 복사해 필수 파일 일체를 가져옴)
def _find_template_name() -> str:
    """환경변수 또는 자동 감지로 템플릿 프로젝트명 결정."""
    env_name = os.environ.get("VIBECUT_TEMPLATE_NAME")
    if env_name:
        return env_name
    # 자동 감지: 첫 번째 프로젝트 폴더
    if CAPCUT_PROJECTS.exists():
        for entry in sorted(CAPCUT_PROJECTS.iterdir()):
            if entry.is_dir() and (entry / "draft_info.json").exists():
                return entry.name
    return "0530"  # fallback (개발 환경)

TEMPLATE_NAME = _find_template_name()
TEMPLATE_DIR  = CAPCUT_PROJECTS / TEMPLATE_NAME

FPS = 30

# ──────────────────────────────────────────
# 기본 자막 스타일 (템플릿 프로젝트에 texts가 없을 때 fallback)
# 서울한강체 B, 크기 6, 그림자, 검은 외곽선
# CapCut 프로젝트의 첫 자막 스타일에서 추출한 값
# ──────────────────────────────────────────
_DEFAULT_TEXT_CONTENT = {
    "styles": [{
        "fill": {"content": {"solid": {"color": [1, 1, 1]}, "render_type": "solid"}},
        "range": [0, 1],
        "shadows": [{
            "thickness_projection_distance": 0,
            "thickness_projection_angle": -45,
            "diffuse": 0.05,
            "distance": 5.0,
            "content": {"solid": {"color": [0, 0, 0]}, "render_type": "solid"},
            "thickness_projection_enable": False,
            "angle": -45,
        }],
        "size": 6,
        "font": {
            "path": "/Users/seungryk/Library/Containers/com.lemon.lvoverseas/Data/Movies/"
                    "CapCut/User Data/Cache/effect/7480847118538706181/"
                    "892de34daab569720c6dbc43537e8cf5/font.ttf",
            "id": "7480847118538706181",
        },
    }],
    "text": "",
}

DEFAULT_TEXT_MATERIAL: dict = {
    "recognize_task_id": "", "id": "", "name": "", "recognize_text": "",
    "recognize_model": "", "punc_model": "", "type": "text",
    "content": json.dumps(_DEFAULT_TEXT_CONTENT, ensure_ascii=False),
    "base_content": "",
    "words": {"start_time": [], "end_time": [], "text": []},
    "current_words": {"start_time": [], "end_time": [], "text": []},
    "global_alpha": 1.0,
    "combo_info": {"text_templates": []},
    "caption_template_info": {
        "resource_id": "", "third_resource_id": "", "resource_name": "",
        "category_id": "", "category_name": "", "effect_id": "", "request_id": "",
        "path": "", "is_new": False, "source_platform": 0,
    },
    "layer_weight": 0, "letter_spacing": 0.0, "text_curve": None,
    "text_loop_on_path": False, "offset_on_path": 0.0,
    "enable_path_typesetting": False, "text_exceeds_path_process_type": 0,
    "text_typesetting_paths": None, "text_typesetting_paths_file": "",
    "text_typesetting_path_index": 0,
    "line_spacing": 0.02,
    "has_shadow": True, "shadow_color": "#000000", "shadow_alpha": 1.0,
    "shadow_smoothing": 0.9, "shadow_distance": 5.0,
    "shadow_point": {"x": 0.6363961030678928, "y": -0.6363961030678927},
    "shadow_angle": -45.0, "shadow_thickness_projection_enable": False,
    "shadow_thickness_projection_angle": 0.0, "shadow_thickness_projection_distance": 0.0,
    "border_alpha": 1.0, "border_color": "#000000", "border_width": 0.15, "border_mode": 0,
    "style_name": "", "text_color": "", "text_alpha": 1.0,
    "font_name": "", "font_title": "",
    "font_size": 6.0,
    "font_path": "/Users/seungryk/Library/Containers/com.lemon.lvoverseas/Data/Movies/"
                 "CapCut/User Data/Cache/effect/7480847118538706181/"
                 "892de34daab569720c6dbc43537e8cf5/font.ttf",
    "font_id": "", "font_resource_id": "7480847118538706181",
    "initial_scale": 0.0, "font_url": "", "typesetting": 0, "alignment": 1, "line_feed": 1,
    "use_effect_default_color": True, "is_rich_text": False,
    "shape_clip_x": False, "shape_clip_y": False, "ktv_color": "",
    "text_to_audio_ids": [], "bold_width": 0.0, "italic_degree": 0,
    "underline": False, "underline_width": 0.05, "underline_offset": 0.22,
    "sub_type": 0, "check_flag": 39, "text_size": 30,
    "font_category_name": "", "font_source_platform": 1,
    "font_third_resource_id": "", "font_category_id": "",
    "fonts": [{
        "id": "", "resource_id": "7480847118538706181", "third_resource_id": "",
        "category_id": "preset", "category_name": "사전 설정", "source_platform": 1,
        "path": "/Users/seungryk/Library/Containers/com.lemon.lvoverseas/Data/Movies/"
                "CapCut/User Data/Cache/effect/7480847118538706181/"
                "892de34daab569720c6dbc43537e8cf5/font.ttf",
        "effect_id": "7480847118538706181", "title": "서울한강체 B",
        "team_id": "", "file_uri": "", "request_id": "",
    }],
    "add_type": 0, "operation_type": 0, "recognize_type": 0,
    "background_color": "", "background_alpha": 1.0, "background_style": 0,
    "background_round_radius": 0.0, "background_width": 0.14,
    "background_height": 0.14, "background_vertical_offset": 0.0,
    "background_horizontal_offset": 0.0, "background_fill": "",
    "single_char_bg_enable": False, "single_char_bg_color": "", "single_char_bg_alpha": 1.0,
    "single_char_bg_round_radius": 0.3, "single_char_bg_width": 0.0,
    "single_char_bg_height": 0.0, "single_char_bg_vertical_offset": 0.0,
    "single_char_bg_horizontal_offset": 0.0,
    "font_team_id": "", "tts_auto_update": False, "text_preset_resource_id": "",
    "group_id": "", "preset_id": "", "preset_name": "", "preset_category": "",
    "preset_category_id": "", "preset_index": 0, "preset_has_set_alignment": False,
    "force_apply_line_max_width": False, "language": "", "relevance_segment": [],
    "original_size": [], "fixed_width": -1.0, "fixed_height": -1.0,
    "line_max_width": 0.82, "oneline_cutoff": False, "cutoff_postfix": "",
    "subtitle_template_original_fontsize": 0.0, "subtitle_keywords": None,
    "inner_padding": -1.0, "multi_language_current": "none", "source_from": "",
    "is_lyric_effect": False, "lyric_group_id": "", "ssml_content": "",
    "subtitle_keywords_config": None, "sub_template_id": -1, "translate_original_text": "",
}

DEFAULT_TEXT_SEGMENT: dict = {
    "id": "", "source_timerange": None,
    "target_timerange": {"start": 0, "duration": 1000000},
    "render_timerange": {"start": 0, "duration": 0},
    "desc": "", "state": 0, "speed": 1.0, "is_loop": False, "is_tone_modify": False,
    "reverse": False, "intensifies_audio": False, "cartoon": False,
    "volume": 1.0, "last_nonzero_volume": 1.0,
    "clip": {
        "scale": {"x": 1.0, "y": 1.0}, "rotation": 0.0,
        "transform": {"x": 0.0, "y": -0.7407407407407407},
        "flip": {"vertical": False, "horizontal": False}, "alpha": 1.0,
    },
    "uniform_scale": {"on": True, "value": 1.0},
    "material_id": "", "extra_material_refs": [], "render_index": 14000,
    "keyframe_refs": [], "enable_lut": False, "enable_adjust": False,
    "enable_hsl": False, "visible": True, "group_id": "",
    "enable_color_curves": True, "enable_hsl_curves": True, "track_render_index": 1,
    "hdr_settings": None, "enable_color_wheels": True, "track_attribute": 0,
    "is_placeholder": False, "template_id": "", "enable_smart_color_adjust": False,
    "template_scene": "default", "common_keyframes": [], "caption_info": None,
    "responsive_layout": {
        "enable": False, "target_follow": "", "size_layout": 0,
        "horizontal_pos_layout": 0, "vertical_pos_layout": 0,
    },
    "enable_color_match_adjust": False, "enable_color_correct_adjust": False,
    "enable_adjust_mask": False, "raw_segment_id": "", "lyric_keyframes": None,
    "enable_video_mask": True, "digital_human_template_group_id": "",
    "color_correct_alg_result": "", "source": "segmentsourcenormal",
    "enable_mask_stroke": False, "enable_mask_shadow": False,
    "enable_color_adjust_pro": False,
}


# ──────────────────────────────────────────
# 유틸
# ──────────────────────────────────────────
def new_id() -> str:
    return str(uuid.uuid4()).upper()


def frame_to_us(frame: int) -> int:
    n = frame * 1_000_000
    r = n // FPS
    if n % FPS * 2 >= FPS:
        r += 1
    return r


def snap_to_frame(seconds: float) -> int:
    return frame_to_us(round(seconds * FPS))


def split_subtitle(text: str, max_chars: int = 18) -> list[str]:
    """
    긴 자막을 여러 세그먼트로 분리 (개행 없이 별도 자막으로 표시).
    한 줄 최대 18글자 기준, 공백 단위로 끊음.
    반환: ["첫 번째 자막", "두 번째 자막", ...]

    ⚠️ 시간 동기화가 필요한 경우 split_with_word_sync()를 사용할 것
    """
    if len(text) <= max_chars:
        return [text]

    words = text.split(" ")
    parts, current = [], ""
    for word in words:
        candidate = (current + " " + word).strip() if current else word
        if len(candidate) <= max_chars:
            current = candidate
        else:
            if current:
                parts.append(current)
            while len(word) > max_chars:
                parts.append(word[:max_chars])
                word = word[max_chars:]
            current = word
    if current:
        parts.append(current)
    return parts if parts else [text]


def merge_verified_text_with_words(verified_segments: list[dict],
                                    word_segments: list[dict]) -> list[dict]:
    """
    검증된 SRT + Whisper 단어 타임스탬프를 병합 (순차 매칭).

    각 단어는 정확히 한 자막에만 할당 → 자막 간 시간 겹침/중복 매칭 방지.

    알고리즘:
    1. 모든 단어를 시간 순서대로 평탄화 (전역 큐)
    2. 각 검증 자막에 대해:
       - 검증 단어 수만큼 큐에서 단어를 순차적으로 꺼냄
       - 단, 다음 자막에 너무 가까운 단어는 남겨둠 (시간 여유 확보)
    3. 큐의 단어 시간을 그대로 활용

    반환: [{start, end, text, words: [{start, end, word}, ...]}, ...]
    """
    # 모든 단어를 시간 순서대로 평탄화
    all_words = []
    for ws in word_segments:
        all_words.extend(ws.get("words", []))

    if not all_words:
        print("  ⚠ 단어 타임스탬프 없음 — 균등 분할로 폴백")
        return [{**vs, "words": []} for vs in verified_segments]

    # 큐 인덱스
    word_idx = 0
    merged = []

    for vs_i, vs in enumerate(verified_segments):
        text = vs.get("text", "").strip()
        if not text:
            merged.append({**vs, "words": []})
            continue

        verified_words = text.split()
        n_need = len(verified_words)

        if word_idx >= len(all_words):
            # 큐 소진 → 시간 정보 없이
            merged.append({**vs, "words": []})
            continue

        # 다음 자막의 시작 시간 (있다면) — 이 시간 이전의 단어만 사용
        next_vs_start = None
        for next_vs in verified_segments[vs_i + 1:]:
            if next_vs.get("text", "").strip():
                next_vs_start = next_vs["start"]
                break

        # 큐에서 단어 가져오기
        # - 기본: 검증 단어 수(n_need)만큼
        # - 단, 다음 자막의 vs.start 이후에 시작하는 단어는 다음 자막용으로 남김
        take_indices = []
        i = word_idx
        while i < len(all_words) and len(take_indices) < n_need:
            w = all_words[i]
            # 다음 자막의 시작 시간을 넘는 단어는 남김 (단, 최소 1개는 가져옴)
            if next_vs_start is not None and w["start"] >= next_vs_start and take_indices:
                break
            take_indices.append(i)
            i += 1

        if not take_indices:
            merged.append({**vs, "words": []})
            continue

        taken = [all_words[idx] for idx in take_indices]
        word_idx = take_indices[-1] + 1

        # 검증 단어 수와 가져온 단어 수가 다를 수 있으므로 비율 매칭
        n_taken = len(taken)
        new_words = []
        for j, vw in enumerate(verified_words):
            lo = int(j * n_taken / n_need)
            hi = max(lo + 1, int((j + 1) * n_taken / n_need))
            hi = min(hi, n_taken)
            slot = taken[lo:hi]
            if not slot:
                slot = [taken[min(lo, n_taken - 1)]]
            new_words.append({
                "start": slot[0]["start"],
                "end": slot[-1]["end"],
                "word": (" " if j > 0 else "") + vw,
            })

        merged.append({
            "start": taken[0]["start"],
            "end": taken[-1]["end"],
            "text": text,
            "words": new_words,
        })

    return merged


def remove_overlaps(parts: list[dict], min_gap: float = 0.02) -> list[dict]:
    """
    인접한 자막의 시간 겹침을 제거.
    parts는 start 기준 정렬되어 있어야 함.

    동작:
    - part[i].end가 part[i+1].start보다 크면 → part[i].end를 part[i+1].start - min_gap로 조정
    - part[i].end < part[i].start가 되면 part[i]를 매우 짧게 (최소 0.1초) 표시
    """
    if len(parts) < 2:
        return parts

    sorted_parts = sorted(parts, key=lambda p: p["start"])
    result = []
    for i, part in enumerate(sorted_parts):
        adjusted = dict(part)
        if i < len(sorted_parts) - 1:
            next_start = sorted_parts[i + 1]["start"]
            if adjusted["end"] > next_start - min_gap:
                adjusted["end"] = max(adjusted["start"] + 0.1, next_start - min_gap)
        # 끝이 시작보다 빠르면 최소 길이 보장
        if adjusted["end"] <= adjusted["start"]:
            adjusted["end"] = adjusted["start"] + 0.1
        result.append(adjusted)
    return result


def split_with_word_sync(seg: dict, max_chars: int = 18) -> list[dict]:
    """
    단어 타임스탬프를 활용한 자막 분리 (실제 발화 시간에 동기화).

    입력: {start, end, text, words: [{start, end, word}, ...]}
    출력: [{start, end, text}, ...]  ← 각 파트가 실제 단어 발화 시간을 가짐

    동작:
    - words 배열을 따라가며 max_chars 이내에서 끊음
    - 각 파트의 start = 첫 단어의 start, end = 마지막 단어의 end
    - words가 없으면 균등 분할로 폴백
    """
    words = seg.get("words", [])
    text = seg.get("text", "").strip()
    seg_start = seg["start"]
    seg_end = seg["end"]

    if not text:
        return []

    # words가 없거나 부실한 경우 → 폴백 (시간 균등 분할)
    if not words:
        parts = split_subtitle(text, max_chars)
        if len(parts) == 1:
            return [{"start": seg_start, "end": seg_end, "text": text}]
        dur = (seg_end - seg_start) / len(parts)
        return [
            {"start": seg_start + dur * i,
             "end": seg_start + dur * (i + 1) if i < len(parts) - 1 else seg_end,
             "text": p}
            for i, p in enumerate(parts)
        ]

    # 단어를 max_chars 단위로 그룹화
    parts = []
    current_words = []
    current_len = 0

    for w in words:
        word_text = w["word"]  # 보통 앞에 공백 포함됨 (예: " 안녕")
        word_len = len(word_text.strip())  # 글자 수 계산 시 공백 제외
        if not word_len:
            continue

        # 현재 그룹에 추가할 수 있는지 확인
        # 글자 수 = 현재 텍스트 + 공백(공백구분이면) + 새 단어
        candidate_len = current_len + (1 if current_words else 0) + word_len

        if candidate_len <= max_chars or not current_words:
            # 추가하거나, 그룹이 비어있으면 무조건 추가 (단어가 max_chars 초과여도)
            current_words.append(w)
            current_len = candidate_len if current_words[:-1] else word_len
        else:
            # 새 그룹 시작
            parts.append(current_words)
            current_words = [w]
            current_len = word_len

    if current_words:
        parts.append(current_words)

    # 후처리: 관형사·지시사가 그룹 마지막 단어면 다음 그룹으로 이동
    # 예: ["...활용해서", "이"] → ["...활용해서"] / ["이", "스킬을 ..."]
    _DANGLING_ARTICLES = {"이", "그", "저", "이런", "그런", "저런", "한", "어떤", "각", "제"}
    for j in range(len(parts) - 1):
        if parts[j] and parts[j][-1]["word"].strip() in _DANGLING_ARTICLES:
            moved = parts[j].pop()
            parts[j + 1].insert(0, moved)

    # 각 그룹을 자막 dict로 변환
    result = []
    for group in parts:
        group_text = "".join(w["word"] for w in group).strip()
        if not group_text:
            continue
        result.append({
            "start": group[0]["start"],
            "end": group[-1]["end"],
            "text": group_text,
        })

    # 후처리: 짧은 종결어미/꼬리 파트를 앞 파트에 머지
    # 예: "프롬프트만 적어줘도" + "됩니다." → "프롬프트만 적어줘도 됩니다."
    result = _merge_short_tails(result, max_chars=max_chars)

    return result if result else [{"start": seg_start, "end": seg_end, "text": text}]


# 한국어 짧은 종결어미·조사 패턴 (선택적 머지 보조)
_SHORT_TAIL_ENDINGS = (
    "다", "다.", "요", "요.", "죠", "죠.", "네", "네.", "까", "까?",
    "함", "함.", "음", "음.", "임", "임.", "지", "지.", "요!", "다!",
)


def merge_cross_segment_parts(
    parts: list[dict],
    max_gap: float = 1.0,
    max_merged_chars: int = 32,
) -> list[dict]:
    """서로 다른 Whisper 세그먼트에서 나온 인접 파트 중 문장이 미완성으로 끊긴 경우를 병합.

    컷편집 이음매에서 Whisper가 하나의 문장을 두 세그먼트로 나누면
    _merge_short_tails()가 처리하지 못하는 cross-segment 케이스를 여기서 처리.

    조건 (모두 충족 시 병합):
    1. prev.text가 종결어미(_SHORT_TAIL_ENDINGS)로 끝나지 않음  ← 문장 미완성
    2. next.start - prev.end ≤ max_gap (기본 0.5초)             ← 바로 이어지는 파트
    3. 합친 길이 ≤ max_merged_chars (기본 27자)                  ← 한 자막에 담을 수 있음
    """
    if len(parts) < 2:
        return parts

    merged = [parts[0]]
    for part in parts[1:]:
        prev = merged[-1]
        gap = part["start"] - prev["end"]
        merged_len = len(prev["text"]) + 1 + len(part["text"])
        is_complete = prev["text"].endswith(_SHORT_TAIL_ENDINGS)

        if not is_complete and gap <= max_gap and merged_len <= max_merged_chars:
            prev["text"] = prev["text"] + " " + part["text"]
            prev["end"] = part["end"]
        else:
            merged.append(part)
    return merged


def split_by_splits(segments: list[dict], splits_data: list[list[str]]) -> list[dict]:
    """AI 분할 결과(splits_data)와 단어 타임스탬프를 매핑해 자막 파트 리스트 반환.

    splits_data: [[part1, part2, ...], ...] — segments와 1:1 대응 (subtitle-splitter 출력)
    반환: [{start, end, text}, ...] (기존 split_with_word_sync 결과와 동일 형태)

    단어 매핑 알고리즘:
    - 각 파트의 텍스트를 공백 제거 후 정규화
    - 세그먼트의 words 배열을 순서대로 소비하면서 누적 텍스트가 파트와 일치하면 경계 확정
    - 마지막 파트는 남은 단어 모두 할당
    - words가 없으면 시간 균등 분할 폴백
    """
    result = []
    for i, seg in enumerate(segments):
        if not seg.get("text", "").strip():
            continue

        if i >= len(splits_data) or not splits_data[i]:
            result.extend(split_with_word_sync(seg, max_chars=18))
            continue

        parts_text = [p for p in splits_data[i] if p.strip()]
        if not parts_text:
            result.extend(split_with_word_sync(seg, max_chars=18))
            continue

        words = seg.get("words", [])
        seg_start = seg["start"]
        seg_end = seg["end"]

        if not words or len(parts_text) == 1:
            # 단어 없거나 분할 없음
            dur = (seg_end - seg_start) / max(len(parts_text), 1)
            for j, pt in enumerate(parts_text):
                result.append({
                    "start": seg_start + dur * j,
                    "end": seg_start + dur * (j + 1) if j < len(parts_text) - 1 else seg_end,
                    "text": pt,
                })
            continue

        # 단어를 각 파트의 텍스트에 순차적으로 greedy 매핑
        word_idx = 0
        for j, pt in enumerate(parts_text):
            is_last = (j == len(parts_text) - 1)
            pt_norm = pt.replace(" ", "")

            if is_last:
                # 마지막 파트: 남은 단어 전부
                part_words = words[word_idx:]
            else:
                part_words = []
                accumulated = ""
                while word_idx < len(words):
                    w = words[word_idx]
                    accumulated += w["word"].strip()
                    part_words.append(w)
                    word_idx += 1
                    if accumulated.replace(" ", "") == pt_norm:
                        break
                    # 누적이 목표를 초과하면 단어 경계가 어긋난 것 — 이대로 사용
                    if len(accumulated.replace(" ", "")) >= len(pt_norm):
                        break

            if part_words:
                result.append({
                    "start": part_words[0]["start"],
                    "end": part_words[-1]["end"],
                    "text": pt,
                })
            else:
                # 단어 매핑 실패 (빈 파트) — 세그먼트 균등 시간 사용
                dur = (seg_end - seg_start) / len(parts_text)
                result.append({
                    "start": seg_start + dur * j,
                    "end": seg_start + dur * (j + 1) if not is_last else seg_end,
                    "text": pt,
                })

    return result


def dump_subtitle_input(segments: list[dict], out_path: str = "/tmp/subtitle_input.json"):
    """Whisper 세그먼트를 subtitle-splitter 에이전트용 JSON으로 덤프.

    출력 형식: [{"id": int, "text": str, "words": [{start, end, word}, ...]}, ...]
    """
    data = [
        {"id": i, "text": seg.get("text", "").strip(), "words": seg.get("words", [])}
        for i, seg in enumerate(segments)
        if seg.get("text", "").strip()
    ]
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"  → subtitle_input.json 저장: {out_path} ({len(data)}개 세그먼트)")


def _merge_short_tails(parts: list[dict], max_chars: int = 18,
                       min_tail_chars: int = 5,
                       max_merged_chars: int = 27) -> list[dict]:
    """
    짧은 꼬리 파트를 직전 파트에 머지.

    규칙:
    1. 파트 텍스트 길이 ≤ min_tail_chars (기본 5자) OR 한국어 종결어미로 끝남
    2. 머지 후 길이 ≤ max_merged_chars (기본 27자, max_chars의 1.5배) 유지
    3. 머지 시 텍스트 = 앞 + " " + 뒤, end = 뒤의 end
    """
    if len(parts) < 2:
        return parts

    merged = [parts[0]]
    for part in parts[1:]:
        prev = merged[-1]
        is_short = len(part["text"]) <= min_tail_chars
        is_ending = part["text"].endswith(_SHORT_TAIL_ENDINGS) and len(part["text"]) <= 8
        merged_len = len(prev["text"]) + 1 + len(part["text"])  # +1 for space

        if (is_short or is_ending) and merged_len <= max_merged_chars:
            # 앞에 머지
            prev["text"] = prev["text"] + " " + part["text"]
            prev["end"] = part["end"]
        else:
            merged.append(part)
    return merged


# ── 사진/영상 구분 ──────────────────────────────────────────────────────────────
# 실측 (CapCut 0602 프로젝트, JPEG 직접 임포트):
#   - type: "photo"          ← "video" 가 아님
#   - duration: 10_800_000_000  ← 3시간 고정값 (표시 시간과 무관)
#   - extra_material_refs: 6개 ← loudness 재료 없음 (영상은 7개)
#   - source_timerange.duration: 실제 표시 시간 (target_timerange 와 동일)
#
# 영상과 사진의 차이 요약:
#   필드                  영상(video)         사진(photo)
#   type                  "video"             "photo"
#   duration              실제 길이(µs)        10_800_000_000
#   extra_material_refs   7개(loudness 포함)   6개(loudness 없음)
#   source_timerange      클립 구간            표시 시간(=target)
PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".heic", ".webp", ".tiff", ".bmp"}
PHOTO_DURATION_US = 10_800_000_000  # CapCut 내부 고정값: 3시간


def is_photo(path: Path) -> bool:
    """파일 확장자로 사진 여부 판단"""
    return path.suffix.lower() in PHOTO_EXTENSIONS


def extract_audio_from_segments(video_path: Path, segments: list[list[float]], out_wav: Path) -> None:
    """편집된 구간만 오디오를 추출·연결해 WAV로 저장.

    segments: [[start_sec, end_sec], ...] — auto-edit이 남긴 발화 구간
    연결된 오디오의 타임스탬프 = CapCut 편집 타임라인 타임스탬프 (1:1 대응)
    """
    if not segments:
        raise ValueError("segments가 비어 있음")

    # ffmpeg filter_complex로 각 구간을 잘라 연결
    inputs = ["-i", str(video_path)]
    filter_parts = []
    for i, (s, e) in enumerate(segments):
        filter_parts.append(f"[0:a]atrim=start={s}:end={e},asetpts=PTS-STARTPTS[a{i}]")
    concat_inputs = "".join(f"[a{i}]" for i in range(len(segments)))
    filter_parts.append(f"{concat_inputs}concat=n={len(segments)}:v=0:a=1[aout]")

    cmd = ["ffmpeg", "-y"] + inputs + [
        "-filter_complex", ";".join(filter_parts),
        "-map", "[aout]",
        "-ar", "16000", "-ac", "1",
        str(out_wav)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print("ffmpeg 오디오 추출 오류:", result.stderr[-800:], file=sys.stderr)
        sys.exit(1)
    print(f"  → 편집 구간 오디오 추출: {len(segments)}개 구간, {out_wav.name}")


def get_video_duration_us(video_path: Path) -> int:
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(video_path)],
        capture_output=True, text=True
    )
    return int(float(result.stdout.strip()) * 1_000_000)


# ──────────────────────────────────────────
# Step 0: CapCut 종료
# ──────────────────────────────────────────
def quit_capcut():
    result = subprocess.run(["pgrep", "-x", "CapCut"], capture_output=True)
    if result.returncode != 0:
        print("  CapCut 실행 중 아님 — 계속 진행")
        return
    print("  CapCut 종료 중...")
    subprocess.run(["pkill", "-x", "CapCut"])
    time.sleep(2)
    print("  CapCut 종료됨")


# ──────────────────────────────────────────
# Step 1: Whisper 전사
# ──────────────────────────────────────────
def extract_audio(video_path: Path) -> Path:
    """영상에서 16kHz mono WAV 오디오 추출 (Whisper 최적 포맷).

    캐시({stem}_audio.wav)가 있으면 재사용.
    반환: WAV 경로
    """
    wav_path = video_path.with_name(video_path.stem + "_audio.wav")
    if wav_path.exists():
        print(f"  오디오 캐시 사용: {wav_path.name}")
        return wav_path
    print(f"  오디오 추출 중: {video_path.name} → {wav_path.name}")
    result = subprocess.run(
        ["ffmpeg", "-y", "-i", str(video_path),
         "-vn", "-ar", "16000", "-ac", "1", str(wav_path)],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print("ffmpeg 오디오 추출 오류:", result.stderr[-400:], file=sys.stderr)
        sys.exit(1)
    size_mb = wav_path.stat().st_size / 1024 / 1024
    print(f"  → {wav_path.name} ({size_mb:.1f}MB)")
    return wav_path


def transcribe(video_path: Path, model_name: str = "small", beam_size: int = 1,
               audio_path: Path | None = None) -> list[dict]:
    """faster-whisper로 한국어 전사 + 단어 타임스탬프.

    audio_path: 편집 구간만 추출된 WAV (--segments 모드). 지정 시 그 파일 전사.
    지정 안 하면 video_path에서 오디오를 자동 추출한 WAV로 전사.
    영상 파일을 직접 넘기지 않고 항상 WAV를 경유함.

    반환: [{start, end, text, words: [{start, end, word}, ...]}, ...]
    """
    py = sys.executable
    # 편집 구간 오디오가 명시된 경우 그것을 사용, 아니면 전체 오디오 추출
    if audio_path:
        target = audio_path
        print(f"  모델: {model_name}, beam_size: {beam_size}")
        print(f"  전사 대상: 편집된 오디오 ({audio_path.name})")
    else:
        target = extract_audio(video_path)
        print(f"  모델: {model_name}, beam_size: {beam_size}")
    script = (
        "from faster_whisper import WhisperModel\nimport json\n"
        f'model = WhisperModel("{model_name}", device="cpu", compute_type="int8")\n'
        f'segs, _ = model.transcribe("{target}", language="ko", beam_size={beam_size}, word_timestamps=True)\n'
        "result = []\n"
        "for s in segs:\n"
        "    words = [{'start': w.start, 'end': w.end, 'word': w.word} for w in (s.words or [])]\n"
        "    result.append({'start': s.start, 'end': s.end, 'text': s.text.strip(), 'words': words})\n"
        "print(json.dumps(result, ensure_ascii=False))"
    )
    result = subprocess.run([py, "-c", script], capture_output=True, text=True)
    if result.returncode != 0:
        print("Whisper 오류:", result.stderr[-800:], file=sys.stderr)
        sys.exit(1)
    for line in reversed(result.stdout.strip().splitlines()):
        if line.strip().startswith("["):
            segments = json.loads(line.strip())
            total_words = sum(len(s.get("words", [])) for s in segments)
            print(f"  → {len(segments)}개 구간 / {total_words}개 단어 인식 완료")
            return segments
    print("전사 결과 파싱 실패", file=sys.stderr)
    sys.exit(1)


def save_word_timestamps(segments: list[dict], out_path: Path):
    """단어 타임스탬프를 JSON으로 저장 (재사용용)"""
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(segments, f, ensure_ascii=False, indent=2)
    print(f"  → 단어 타임스탬프 저장: {out_path.name}")


def load_word_timestamps(json_path: Path) -> list[dict]:
    """저장된 단어 타임스탬프 로드"""
    with open(json_path, encoding="utf-8") as f:
        segments = json.load(f)
    total_words = sum(len(s.get("words", [])) for s in segments)
    print(f"  → {json_path.name}에서 {len(segments)}개 구간 / {total_words}개 단어 로드")
    return segments


def load_srt(srt_path: Path) -> list[dict]:
    """기존 SRT 파일을 [{start, end, text}] 형태로 로드"""
    def parse_ts(s):
        s = s.replace(",", ".")
        h, m, rest = s.split(":")
        return int(h) * 3600 + int(m) * 60 + float(rest)

    segments = []
    for block in srt_path.read_text(encoding="utf-8").strip().split("\n\n"):
        lines = block.strip().splitlines()
        if len(lines) < 3:
            continue
        t = lines[1].split(" --> ")
        segments.append({
            "start": parse_ts(t[0].strip()),
            "end":   parse_ts(t[1].strip()),
            "text":  " ".join(lines[2:]).strip()
        })
    print(f"  → SRT {srt_path.name}에서 {len(segments)}개 로드")
    return segments


def apply_corrections_dictionary(segments: list[dict],
                                 dict_path: Path = None) -> tuple[list[dict], int]:
    """
    누적 오인식 사전(corrections.json)을 자막에 1차 적용.
    LLM 검수 전에 알려진 오인식을 자동으로 교정해 LLM 부담 감소.

    반환: (교정된 segments, 교정 건수)
    """
    if dict_path is None:
        # 환경변수 → data/corrections.json (Vibecut 구조) → ../corrections.json (구버전)
        env_path = os.environ.get("VIBECUT_CORRECTIONS")
        if env_path:
            dict_path = Path(env_path)
        else:
            new_loc = Path(__file__).parent.parent / "data" / "corrections.json"
            old_loc = Path(__file__).parent.parent / "corrections.json"
            dict_path = new_loc if new_loc.exists() else old_loc

    if not dict_path.exists():
        return segments, 0

    with open(dict_path, encoding="utf-8") as f:
        data = json.load(f)
    dictionary = data.get("dictionary", {})
    if not dictionary:
        return segments, 0

    # 긴 키부터 매칭 (예: "코덱스 리그젝"이 "리그젝"보다 먼저)
    sorted_keys = sorted(dictionary.keys(), key=len, reverse=True)

    correction_count = 0
    for seg in segments:
        text = seg.get("text", "")
        original = text
        for wrong in sorted_keys:
            if wrong in text:
                text = text.replace(wrong, dictionary[wrong])
        if text != original:
            seg["text"] = text
            correction_count += 1

    return segments, correction_count


def update_corrections_dictionary(new_corrections: dict,
                                  dict_path: Path = None) -> int:
    """
    새로 발견된 오인식 패턴을 사전에 추가.
    subtitle-verifier가 검수 후 발견한 패턴을 호출.

    반환: 추가된 항목 수
    """
    if dict_path is None:
        # 환경변수 → data/corrections.json (Vibecut 구조) → ../corrections.json (구버전)
        env_path = os.environ.get("VIBECUT_CORRECTIONS")
        if env_path:
            dict_path = Path(env_path)
        else:
            new_loc = Path(__file__).parent.parent / "data" / "corrections.json"
            old_loc = Path(__file__).parent.parent / "corrections.json"
            dict_path = new_loc if new_loc.exists() else old_loc

    if dict_path.exists():
        with open(dict_path, encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = {
            "_meta": {"description": "Whisper 오인식 교정 사전", "version": 1},
            "dictionary": {}
        }

    existing = data.get("dictionary", {})
    added = 0
    for wrong, right in new_corrections.items():
        if wrong not in existing and wrong != right:
            existing[wrong] = right
            added += 1

    data["dictionary"] = existing
    data["_meta"]["last_updated"] = time.strftime("%Y-%m-%d")

    with open(dict_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    return added


def apply_subtitle_outline(text_material: dict, border_width: float = 0.15) -> None:
    """
    자막에 검은 외곽선 적용 (흰 배경에서도 잘 보이게).

    두 위치 동시 설정:
    - text_material["border_*"]: CapCut UI 메타데이터
    - content.styles[].strokes: 실제 영상 렌더링용

    border_width: 0.10(얇음) ~ 0.20(두꺼움) 권장. 기본 0.15.
    """
    text_material["border_color"] = "#000000"
    text_material["border_width"] = border_width
    text_material["border_alpha"] = 1.0
    text_material["border_mode"] = 0

    c = json.loads(text_material["content"])
    stroke = {
        "content": {
            "render_type": "solid",
            "solid": {"alpha": 1.0, "color": [0.0, 0.0, 0.0]},
        },
        "width": border_width,
    }
    for st in c.get("styles", []):
        st["strokes"] = [stroke]
    text_material["content"] = json.dumps(c, ensure_ascii=False)


def save_srt(segments: list[dict], out_path: Path):
    def ts(s):
        h = int(s // 3600); m = int((s % 3600) // 60); sec = s % 60
        return f"{h:02d}:{m:02d}:{sec:06.3f}".replace(".", ",")
    with open(out_path, "w", encoding="utf-8") as f:
        for i, seg in enumerate(segments, 1):
            f.write(f"{i}\n{ts(seg['start'])} --> {ts(seg['end'])}\n{seg['text']}\n\n")
    print(f"  → SRT 저장: {out_path}")


# ──────────────────────────────────────────
# Step 1-b: 자막 검증 (Claude Code 에이전트로 처리)
# ──────────────────────────────────────────
# 검증은 Python 스크립트가 아니라 .claude/agents/subtitle-verifier/AGENT.md에서 수행한다.
# 흐름:
#   1) add_subtitles.py가 Whisper로 <name>.srt 생성
#   2) 사용자가 자연어로 "@subtitle-verifier 검증해줘" 또는 "/agents subtitle-verifier" 요청
#   3) 에이전트가 <name>_verified.srt 생성
#   4) add_subtitles.py를 --srt <name>_verified.srt 옵션으로 재실행해 검증된 자막 적용
#
# 이렇게 분리하는 이유:
# - Python에서 anthropic SDK를 직접 호출하면 API 키 관리 부담
# - Claude Code 세션의 컨텍스트(영상 주제, 사용자 선호)를 활용한 더 정확한 교정 가능
# - 에이전트는 Read/Write/Edit 도구로 SRT를 직접 다룸


# ──────────────────────────────────────────
# Step 2: CapCut 프로젝트 생성
# ──────────────────────────────────────────
def create_project(video_path: Path, segments: list[dict], project_name: str,
                   splits_data: list[list[str]] | None = None) -> tuple[Path, str, int]:
    project_dir = CAPCUT_PROJECTS / project_name

    # ── 2-0. 기존 프로젝트 자동 백업 (덮어쓰기 안전장치) ───────────────
    if project_dir.exists():
        try:
            from _lib_backup import backup_project_json
            backup_project_json(project_dir, tag="add_subtitles")
        except Exception as e:
            print(f"  ⚠ 백업 건너뜀: {e}")

    # ── 2-1. 프로젝트 디렉토리 준비 ────────────────────────────────────
    # [A] 템플릿이 있을 때: 기존 프로젝트 삭제 후 템플릿 전체 복사
    #     (CapCut이 요구하는 attachment_editing.json 등 부수 파일을 모두 포함)
    # [B] 템플릿이 없을 때: 기존 project_dir을 그대로 사용 (삭제·복사 없음)
    #     → 기존 컷편집 프로젝트에 자막만 주입할 때 안전하게 동작
    use_template = TEMPLATE_DIR.exists()

    if use_template:
        if project_dir.exists():
            shutil.rmtree(project_dir)
        shutil.copytree(str(TEMPLATE_DIR), str(project_dir))
        print(f"  템플릿 복사: {TEMPLATE_NAME} → {project_name}")
    else:
        if not project_dir.exists():
            raise FileNotFoundError(
                f"템플릿({TEMPLATE_DIR})도 없고 대상 프로젝트({project_dir})도 없음. "
                "CapCut에서 프로젝트를 먼저 만들거나 TEMPLATE_NAME을 설정하세요."
            )
        print(f"  ⚠ 템플릿 없음 — 기존 프로젝트 재사용: {project_name}")

    # ── 2-2. Timeline UUID 결정 ────────────────────────────────────────
    if use_template:
        # 템플릿 UUID를 새 UUID로 교체 (두 프로젝트 혼동 방지)
        old_uuid = next(
            e.name for e in (project_dir / "Timelines").iterdir()
            if e.is_dir() and "-" in e.name
        )
        new_timeline_uuid = new_id()
        old_tl_dir = project_dir / "Timelines" / old_uuid
        new_tl_dir = project_dir / "Timelines" / new_timeline_uuid
        old_tl_dir.rename(new_tl_dir)
        print(f"  Timeline UUID: {old_uuid[:8]}… → {new_timeline_uuid[:8]}…")
    else:
        # 기존 프로젝트 UUID 그대로 유지
        new_tl_dir = next(
            e for e in (project_dir / "Timelines").iterdir()
            if e.is_dir() and "-" in e.name
        )
        new_timeline_uuid = new_tl_dir.name
        print(f"  Timeline UUID: {new_timeline_uuid[:8]}… (재사용)")

    # ── 2-3. Timelines/project.json 업데이트 ──────────────────────────
    # (템플릿 모드에서만 재생성 — 기존 프로젝트 모드에서는 유지)
    if use_template:
        now_us = int(time.time() * 1_000_000)
        outer_id = new_id()  # main_timeline_id와 반드시 다른 UUID
        proj_json = {
            "config": {
                "color_space": -1,
                "render_index_track_mode_on": False,
                "use_float_render": False
            },
            "create_time": now_us,
            "id": outer_id,
            "main_timeline_id": new_timeline_uuid,
            "timelines": [{
                "create_time": now_us,
                "id": new_timeline_uuid,
                "is_marked_delete": False,
                "name": "타임라인 01",
                "update_time": now_us
            }],
            "update_time": now_us,
            "version": 0
        }
        proj_str = json.dumps(proj_json, ensure_ascii=False, indent=2)
        (project_dir / "Timelines" / "project.json").write_text(proj_str, encoding="utf-8")
        (project_dir / "Timelines" / "project.json.bak").write_text(proj_str, encoding="utf-8")

    # ── 2-4. draft_info.json 로드 ──────────────────────────────────────
    # 템플릿 모드: TEMPLATE_DIR/draft_info.json 사용
    # 기존 프로젝트 모드: project_dir 내 Timelines/{uuid}/draft_info.json 사용
    draft_source = TEMPLATE_DIR / "draft_info.json" if use_template \
                   else new_tl_dir / "draft_info.json"
    with open(draft_source) as f:
        draft = json.load(f)

    video_dur_us = get_video_duration_us(video_path)
    new_project_id = new_id()

    draft["id"]       = new_project_id
    draft["name"]     = project_name
    draft["duration"] = video_dur_us
    draft["path"]     = ""

    # 미디어 트랙 (단일 세그먼트)
    # 사진과 영상의 material 포맷이 다름 — 실측 기반 분기 처리
    input_is_photo = is_photo(video_path)
    vid_id, spd_id, plc_id = new_id(), new_id(), new_id()
    cvs_id, snd_id, col_id, vcl_id = new_id(), new_id(), new_id(), new_id()

    orig_vid = draft["materials"]["videos"][0]
    new_vid = copy.deepcopy(orig_vid)
    if input_is_photo:
        # 사진 전용 포맷 (CapCut 실측):
        #   type="photo", duration=10_800_000_000(3h 고정), has_audio=False
        new_vid.update({
            "id": vid_id,
            "type": "photo",
            "path": str(video_path.resolve()),
            "media_path": "",
            "material_name": video_path.name,
            "local_material_id": new_id(),
            "duration": PHOTO_DURATION_US,
            "has_audio": False,
        })
    else:
        # 영상 포맷 (기존 동작 유지)
        new_vid.update({
            "id": vid_id,
            "path": str(video_path.resolve()),
            "media_path": "",
            "material_name": video_path.name,
            "local_material_id": new_id(),
            "duration": video_dur_us,
        })

    orig_vseg = draft["tracks"][0]["segments"][0]
    new_vseg = copy.deepcopy(orig_vseg)
    if input_is_photo:
        # 사진 세그먼트: extra_refs 6개 (loudness 없음), source_timerange=표시시간
        new_vseg.update({
            "id": new_id(),
            "material_id": vid_id,
            "extra_material_refs": [spd_id, plc_id, cvs_id, snd_id, col_id, vcl_id],
            "source_timerange": {"start": 0, "duration": video_dur_us},
            "target_timerange": {"start": 0, "duration": video_dur_us},
            "speed": 1.0, "volume": 1.0,
            "clip": {
                "alpha": 1.0,
                "flip": {"horizontal": False, "vertical": False},
                "rotation": 0.0,
                "scale": {"x": 1.0, "y": 1.0},
                "transform": {"x": 0.0, "y": 0.0}
            },
        })
    else:
        # 영상 세그먼트: extra_refs 6개 (기존 동작 유지)
        new_vseg.update({
            "id": new_id(),
            "material_id": vid_id,
            "extra_material_refs": [spd_id, plc_id, cvs_id, snd_id, col_id, vcl_id],
            "source_timerange": {"start": 0, "duration": video_dur_us},
            "target_timerange": {"start": 0, "duration": video_dur_us},
            "speed": 1.0, "volume": 1.0,
            "clip": {
                "alpha": 1.0,
                "flip": {"horizontal": False, "vertical": False},
                "rotation": 0.0,
                "scale": {"x": 1.0, "y": 1.0},
                "transform": {"x": 0.0, "y": 0.0}
            },
        })

    video_track = copy.deepcopy(draft["tracks"][0])
    video_track["id"] = new_id()
    video_track["segments"] = [new_vseg]

    # 자막 트랙 (기존 text material 구조를 deepcopy해서 id/content만 교체)
    # 처음부터 조립하면 누락 필드 발생 → 반드시 deepcopy 방식 사용
    # 템플릿/프로젝트에 texts가 없으면 DEFAULT_TEXT_MATERIAL (서울한강체 B) 사용
    _texts = draft["materials"].get("texts", [])
    orig_text = _texts[0] if _texts else copy.deepcopy(DEFAULT_TEXT_MATERIAL)
    _track1_segs = draft["tracks"][1]["segments"] if len(draft["tracks"]) > 1 else []
    orig_tseg = _track1_segs[0] if _track1_segs else copy.deepcopy(DEFAULT_TEXT_SEGMENT)

    text_materials = []
    text_segments  = []

    # 모든 자막을 분리한 뒤, 인접 자막의 시간 겹침 제거
    # splits_data가 있으면 AI 분할 결과를, 없으면 기본 글자 수 분할 사용
    if splits_data is not None:
        all_parts = split_by_splits(segments, splits_data)
        print(f"  AI 분할 적용: {len(all_parts)}개 자막 파트")
    else:
        all_parts = []
        for seg in segments:
            if not seg.get("text", "").strip():
                continue
            parts = split_with_word_sync(seg, max_chars=18)
            all_parts.extend(parts)
        # 컷편집 이음매에서 나뉜 미완성 문장을 cross-segment 병합
        all_parts = merge_cross_segment_parts(all_parts)
    all_parts = remove_overlaps(all_parts, min_gap=0.02)

    render_idx = 0
    for part in all_parts:
        text = part["text"]
        part_start_us = snap_to_frame(part["start"])
        part_end_us = snap_to_frame(part["end"])
        dur_us = part_end_us - part_start_us
        if dur_us <= 0:
            continue
        if True:  # noqa: SIM103 — 인접 코드 들여쓰기 호환용 (12 spaces 유지)

            mat_id = new_id()

            # text material: deepcopy 후 id/content만 교체
            new_text = copy.deepcopy(orig_text)
            new_text["id"] = mat_id
            content_obj = json.loads(orig_text["content"])
            content_obj["text"] = text
            for style in content_obj.get("styles", []):
                style["range"] = [0, len(text)]
            new_text["content"] = json.dumps(content_obj, ensure_ascii=False)

            # 검은 외곽선 적용 (흰 배경에서도 잘 보임)
            apply_subtitle_outline(new_text, border_width=0.15)

            text_materials.append(new_text)

            # text segment
            new_tseg = copy.deepcopy(orig_tseg)
            new_tseg.update({
                "id": new_id(),
                "material_id": mat_id,
                "extra_material_refs": [],
                "source_timerange": None,
                "target_timerange": {"start": part_start_us, "duration": dur_us},
                "render_index": 14000 + render_idx,
            })
            # 자막 위치를 항상 하단 고정 (템플릿/기본값과 무관하게 일관된 위치 보장)
            # y = -0.74: 화면 하단 (CapCut 좌표계: -1=최하단, 0=중앙, 1=최상단)
            new_tseg.setdefault("clip", {}).setdefault("transform", {})
            new_tseg["clip"]["transform"]["y"] = -0.7407407407407407
            text_segments.append(new_tseg)
            render_idx += 1

    # 자막 트랙 구성 — tracks[1]이 없으면 기본 text 트랙 구조로 대체
    if len(draft["tracks"]) > 1:
        text_track = copy.deepcopy(draft["tracks"][1])
        text_track["id"] = new_id()
    else:
        text_track = {
            "id": new_id(),
            "attribute": 0,
            "flag": 0,
            "is_default_name": True,
            "name": "",
            "type": "text",
        }
    text_track["segments"] = text_segments

    # materials 교체
    draft["materials"]["videos"]              = [new_vid]
    draft["materials"]["texts"]               = text_materials
    draft["materials"]["speeds"]              = [{"id": spd_id, "type": "speed", "mode": 0, "speed": 1.0, "curve_speed": None}]
    draft["materials"]["placeholder_infos"]   = [{"id": plc_id, "type": "placeholder_info", "meta_type": "none",
                                                   "res_path": "", "res_text": "", "error_path": "", "error_text": ""}]
    draft["materials"]["canvases"]            = [{"id": cvs_id, "type": "canvas_color", "color": "", "blur": 0.0,
                                                   "image": "", "album_image": "", "image_id": "", "image_name": "",
                                                   "source_platform": 0, "team_id": ""}]
    draft["materials"]["sound_channel_mappings"] = [{"id": snd_id, "type": "none",
                                                     "audio_channel_mapping": 0, "is_config_open": False}]
    draft["materials"]["material_colors"]     = [{"id": col_id, "is_color_clip": False, "is_gradient": False,
                                                   "solid_color": "", "gradient_colors": [], "gradient_percents": [],
                                                   "gradient_angle": 90.0, "width": 0.0, "height": 0.0}]
    draft["materials"]["vocal_separations"]   = [{"id": vcl_id, "type": "vocal_separation", "choice": 0,
                                                   "removed_sounds": [], "time_range": None,
                                                   "production_path": "", "final_algorithm": "", "enter_from": ""}]
    for k in ["audios", "beats", "effects", "transitions", "adjusts", "stickers",
              "masks", "handwrites", "flowers", "digital_humans", "video_effects",
              "video_trackings", "ai_translates"]:
        if k in draft["materials"]:
            draft["materials"][k] = []

    draft["tracks"] = [video_track, text_track]

    # ── 2-5. 4개 파일 동시 저장 ────────────────────────────────────────
    draft_str = json.dumps(draft, ensure_ascii=False, indent=2)
    for p in [
        project_dir / "draft_info.json",
        project_dir / "draft_info.json.bak",
        new_tl_dir  / "draft_info.json",
        new_tl_dir  / "draft_info.json.bak",
    ]:
        p.write_text(draft_str, encoding="utf-8")
    print("  draft_info.json 4개 파일 저장")

    # ── 2-6. 불필요한 파일 제거 ────────────────────────────────────────
    # 0531(CapCut 직접 생성)과 비교 시 없어야 하는 파일들
    # 존재하면 오히려 열리지 않을 수 있음
    for fname in ["attachment_editing.json", "attachment_pc_common.json",
                  "draft.extra", "draft_cover.jpg", "key_value.json",
                  "performance_opt_info.json", "template.tmp"]:
        p = project_dir / fname
        if p.exists(): p.unlink()
    for fname in ["attachment_editing.json", "attachment_pc_common.json",
                  "draft.extra", "draft_cover.jpg"]:
        p = new_tl_dir / fname
        if p.exists(): p.unlink()

    # ── 2-7. .locked 삭제 ──────────────────────────────────────────────
    locked = project_dir / ".locked"
    if locked.exists():
        locked.unlink()
        print("  .locked 삭제")

    return project_dir, new_project_id, video_dur_us


# ──────────────────────────────────────────
# Step 3: root_meta_info.json 등록
# ──────────────────────────────────────────
def register_project(project_dir: Path, project_id: str, project_name: str,
                     duration_us: int, video_path: Path):
    """
    두 곳에 등록:
    1) root_meta_info.json — CapCut 홈 화면 목록
    2) draft_meta_info.json — 프로젝트별 메타 (draft_id, draft_materials 등)

    0531(CapCut 직접 생성) 기준으로 확인된 올바른 형식 사용.
    """
    now_us  = int(time.time() * 1_000_000)
    now_sec = int(time.time())

    # ── 1) root_meta_info.json ─────────────────────────────────────────
    with open(ROOT_META, encoding="utf-8") as f:
        root = json.load(f)

    root["all_draft_store"] = [
        d for d in root["all_draft_store"]
        if f"/{project_name}" not in d.get("draft_fold_path", "")
    ]
    template_entry = next(
        d for d in root["all_draft_store"] if TEMPLATE_NAME in d.get("draft_fold_path", "")
    )
    new_entry = copy.deepcopy(template_entry)
    new_entry.update({
        "draft_id":          project_id,
        "draft_name":        project_name,
        "draft_fold_path":   str(project_dir),
        "draft_json_file":   str(project_dir / "draft_info.json"),
        "draft_cover":       "draft_cover.jpg",  # 상대 경로
        "tm_draft_create":   now_us,
        "tm_draft_modified": now_us,
        "tm_duration":       duration_us,
    })
    root["all_draft_store"].insert(0, new_entry)

    with open(ROOT_META, "w", encoding="utf-8") as f:
        json.dump(root, f, ensure_ascii=False, indent=2)
    print(f"  root_meta_info.json 등록 (draft_id: {project_id})")

    # ── 2) draft_meta_info.json ────────────────────────────────────────
    # 0531 기준: draft_id = 별도 UUID (draft_info.json의 id와 다름)
    # draft_materials에 실제 영상 파일 정보 포함
    meta_id = str(uuid.uuid4()).upper()  # 별도 UUID
    meta = {
        "cloud_draft_cover": False, "cloud_draft_sync": False,
        "cloud_package_completed_time": "",
        "draft_cloud_capcut_purchase_info": "", "draft_cloud_last_action_download": False,
        "draft_cloud_package_type": "", "draft_cloud_purchase_info": "",
        "draft_cloud_template_id": "", "draft_cloud_tutorial_info": "",
        "draft_cloud_videocut_purchase_info": "",
        "draft_cover": "draft_cover.jpg",
        "draft_deeplink_url": "",
        "draft_enterprise_info": {
            "draft_enterprise_extra": "", "draft_enterprise_id": "",
            "draft_enterprise_name": "", "enterprise_material": []
        },
        "draft_fold_path":   str(project_dir),
        "draft_id":          meta_id,       # root_meta와 동일한 별도 UUID (일치 불필요)
        "draft_is_ae_produce": False, "draft_is_ai_packaging_used": False,
        "draft_is_ai_shorts": False, "draft_is_ai_translate": False,
        "draft_is_article_video_draft": False, "draft_is_cloud_temp_draft": False,
        "draft_is_from_deeplink": "false", "draft_is_invisible": False,
        "draft_is_pippit_draft": False, "draft_is_web_article_video": False,
        "draft_materials": [
            {"type": 0, "value": [
                {"ai_group_type": "", "create_time": now_sec,
                 "duration": duration_us, "enter_from": 0,
                 "extra_info": video_path.name,
                 "file_Path": str(video_path),
                 "height": 1080, "width": 1920,
                 "id": str(uuid.uuid4()),
                 "import_time": now_sec, "import_time_ms": now_us,
                 "item_source": 1, "md5": "", "metetype": "video",
                 "roughcut_time_range": {"duration": duration_us, "start": 0},
                 "sub_time_range": {"duration": -1, "start": -1},
                 "type": 0}
            ]},
            {"type": 1, "value": []}, {"type": 2, "value": []},
            {"type": 3, "value": []}, {"type": 6, "value": []}, {"type": 7, "value": []}
        ],
        "draft_materials_copied_info": [],
        "draft_name":        project_name,
        "draft_need_rename_folder": False,
        "draft_new_version": "",
        "draft_removable_storage_device": "",
        "draft_root_path":   str(CAPCUT_PROJECTS),
        "draft_segment_extra_info": [],
        "draft_timeline_materials_size_": 0,
        "draft_type": "",
        "draft_web_article_video_enter_from": "",
        "tm_draft_cloud_completed": "",
        "tm_draft_cloud_entry_id": -1, "tm_draft_cloud_modified": 0,
        "tm_draft_cloud_parent_entry_id": -1, "tm_draft_cloud_space_id": -1,
        "tm_draft_cloud_user_id": -1,
        "tm_draft_create":   now_us,
        "tm_draft_modified": now_us,
        "tm_draft_removed":  0,
        "tm_duration":       duration_us,
    }
    (project_dir / "draft_meta_info.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("  draft_meta_info.json 생성")


# ──────────────────────────────────────────
# Main
# ──────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="영상 → CapCut 자막 프로젝트 자동 생성")
    parser.add_argument("video",           help="입력 영상 파일 (.mov / .mp4)")
    parser.add_argument("--project-name",  default=None,  help="CapCut 프로젝트 이름 (기본: 파일명)")
    parser.add_argument("--srt",           default=None,  help="기존 SRT 파일 경로 (지정 시 Whisper 생략)")
    parser.add_argument("--no-verify",     action="store_true", help="자막 검증 안내 메시지 생략 (검증된 SRT 사용 시)")
    parser.add_argument("--model",         default="small",
                        choices=["tiny", "base", "small", "medium", "large-v3"],
                        help="Whisper 모델 (tiny: 가장 빠름~large-v3: 가장 정확). 기본 small.")
    parser.add_argument("--beam-size",     type=int, default=1,
                        help="Whisper beam_size (클수록 정확, 느림). 기본 1.")
    parser.add_argument("--segments",      default=None,
                        help="auto-edit 결과 segments JSON 경로 (지정 시 편집 타임라인 기준 오디오 추출 후 전사)")
    parser.add_argument("--splits",        default=None,
                        help="subtitle-splitter 분할 결과 JSON 경로 (기본: 없음, 있으면 AI 분할 사용)")
    parser.add_argument("--dump-only",     action="store_true",
                        help="subtitle_input.json 덤프까지만 실행하고 종료 (프로젝트 생성 안 함). "
                             "검증된 SRT 기준으로 splitter 입력을 재덤프할 때 사용.")
    args = parser.parse_args()

    video_path   = Path(args.video).resolve()
    project_name = args.project_name or video_path.stem

    if not video_path.exists():
        print(f"오류: 파일 없음 — {video_path}", file=sys.stderr)
        sys.exit(1)
    if not TEMPLATE_DIR.exists() and not args.dump_only:
        # 템플릿이 없어도 기존 CapCut 프로젝트가 있으면 그대로 자막 주입 가능
        # (서울한강체 B 기본 스타일 사용)
        project_name = args.project_name or video_path.stem
        target_dir = CAPCUT_PROJECTS / project_name
        if not target_dir.exists():
            print(f"오류: 템플릿({TEMPLATE_DIR})도 없고 대상 프로젝트({target_dir})도 없음.",
                  file=sys.stderr)
            print("      CapCut에서 프로젝트를 먼저 만들거나 TEMPLATE_NAME을 설정하세요.")
            sys.exit(1)
        print(f"⚠  템플릿 없음 — 기존 프로젝트에 기본 스타일(서울한강체 B)로 자막 주입: {project_name}")

    # 편집 타임라인 segments 로드 (--segments 지정 시)
    edit_segments: list[list[float]] | None = None
    extracted_audio: Path | None = None
    if args.segments:
        seg_path = Path(args.segments).resolve()
        if not seg_path.exists():
            print(f"오류: segments 파일 없음 — {seg_path}", file=sys.stderr)
            sys.exit(1)
        with open(seg_path, encoding="utf-8") as f:
            edit_segments = json.load(f)
        print(f"\n[0/3] 편집 타임라인 오디오 추출 ({len(edit_segments)}개 구간)")
        extracted_audio = video_path.with_name(video_path.stem + "_edited_audio.wav")
        extract_audio_from_segments(video_path, edit_segments, extracted_audio)

    print("\n[1/3] 음성 인식")
    srt_cache = video_path.with_suffix(".srt")
    verified_srt = video_path.with_name(video_path.stem + "_verified.srt")
    words_cache = video_path.with_name(video_path.stem + "_words.json")  # 단어 타임스탬프 캐시

    # --segments가 지정되면 캐시 무효화 (편집 기준 재전사)
    if edit_segments is not None:
        srt_cache    = video_path.with_name(video_path.stem + "_edited.srt")
        verified_srt = video_path.with_name(video_path.stem + "_edited_verified.srt")
        words_cache  = video_path.with_name(video_path.stem + "_edited_words.json")

    # 단어 타임스탬프 로드/생성 (음성 ↔ 자막 정확한 싱크용)
    word_segments = None
    if words_cache.exists():
        print(f"  단어 타임스탬프 캐시 발견: {words_cache.name}")
        word_segments = load_word_timestamps(words_cache)

    if args.srt:
        segments = load_srt(Path(args.srt))
    elif verified_srt.exists() and not args.no_verify:
        print(f"  검증된 SRT 발견: {verified_srt.name}")
        segments = load_srt(verified_srt)
    elif srt_cache.exists() and word_segments:
        print("  캐시된 SRT + 단어 타임스탬프 발견 — Whisper 생략")
        segments = load_srt(srt_cache)
    else:
        segments = transcribe(video_path, model_name=args.model, beam_size=args.beam_size,
                              audio_path=extracted_audio)
        save_srt(segments, srt_cache)
        save_word_timestamps(segments, words_cache)
        word_segments = segments  # 방금 추출했으므로 동일

    # 누적 오인식 사전 1차 적용 (검증된 SRT가 아닌 경우)
    if not args.srt and not verified_srt.exists():
        segments, dict_fix_count = apply_corrections_dictionary(segments)
        if dict_fix_count > 0:
            print(f"  → 누적 사전 적용: {dict_fix_count}개 자막 자동 교정")

    # 검증된 SRT를 사용한 경우, 원본 단어 타임스탬프와 병합
    if word_segments and (args.srt or verified_srt.exists()):
        print("  검증된 자막과 단어 타임스탬프 병합 중...")
        segments = merge_verified_text_with_words(segments, word_segments)
        merged_words = sum(len(s.get("words", [])) for s in segments)
        print(f"  → {merged_words}개 단어 매칭 완료")

    # 검증 안내 (verified.srt가 없고 --no-verify가 아닌 경우)
    if not args.srt and not verified_srt.exists() and not args.no_verify:
        print("\n  ⚠ 자막 검증 권장")
        print("    Claude Code에서 다음과 같이 요청하세요:")
        print(f"      \"@subtitle-verifier {srt_cache.name} 검증해줘\"")
        print("    검증 후 다시 실행:")
        print(f"      python3 scripts/add_subtitles.py {video_path.name} --srt {verified_srt.name}")
        print("  → 검증 없이 진행합니다 (--no-verify 또는 검증된 SRT가 있으면 이 메시지 생략)")

    # subtitle_input.json 덤프 (subtitle-splitter 서브에이전트용)
    # --srt(검증본) 지정 시 검증된 텍스트 + 단어 타임스탬프 기준으로 덤프됨
    dump_subtitle_input(segments)

    if args.dump_only:
        print("\n✓ --dump-only: subtitle_input.json 덤프 완료 (프로젝트 생성 생략)")
        return

    # --splits: AI 분할 결과 로드
    splits_data: list[list[str]] | None = None
    if args.splits:
        splits_path = Path(args.splits)
        if splits_path.exists():
            with open(splits_path, encoding="utf-8") as _f:
                splits_data = json.load(_f)
            print(f"  AI 분할 결과 로드: {splits_path.name} ({len(splits_data)}개 세그먼트)")
        else:
            print(f"  ⚠ splits 파일 없음 ({splits_path}) — 기본 분할 사용")

    print(f"\n[2/3] CapCut 프로젝트 생성: {project_name}")
    quit_capcut()
    project_dir, project_id, duration_us = create_project(
        video_path, segments, project_name, splits_data=splits_data
    )

    print("\n[3/3] 프로젝트 등록")
    register_project(project_dir, project_id, project_name, duration_us, video_path)

    subtitle_count = sum(1 for s in segments if s.get("text", "").strip())
    print("\n✓ 완료")
    print(f"  프로젝트: {project_dir}")
    print(f"  자막 수:  {subtitle_count}개")
    print(f"  → CapCut을 열어 '{project_name}' 프로젝트를 확인하세요.")


if __name__ == "__main__":
    main()
