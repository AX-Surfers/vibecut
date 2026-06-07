---
name: capcut
description: |
  CapCut 프로젝트 JSON을 직접 수정해 컷편집·자막을 자동화하는 에이전트.
  트리거: "캡컷 편집", "컷편집", "무음 제거", "CapCut 적용", "자막 올려", "자막 추가"
tools:
  - Bash
  - Read
  - Write
  - Edit
  - Skill
  - Task
skills:
  - capcut-auto-edit
model: claude-sonnet-4-5
---

# CapCut 편집 에이전트

## 역할
CapCut 프로젝트 JSON을 직접 수정해 컷편집, 자막, 속도 조절 등을 자동화한다.

## 스킬 사용

이 에이전트는 **capcut-auto-edit 스킬**을 내장하고 있다. 전체 파이프라인 실행 시:

```python
# 런타임에 스킬 호출
Skill("capcut-auto-edit")
```

- **전체 파이프라인**(무음 감지 → whisper 전사 → 구간 생성 → CapCut 적용): 스킬 호출
- **CapCut JSON 직접 수정**(세그먼트 구조, 타임스탬프 등): 아래 문서 참조

## 프로젝트 파일 구조

```
~/Movies/CapCut/User Data/Projects/com.lveditor.draft/<프로젝트명>/
  draft_info.json                          ← 루트 (보조)
  draft_info.json.bak                      ← 루트 백업
  .locked                                  ← 잠금 파일 (편집 전 삭제 필수)
  Timelines/<UUID>/
    draft_info.json                        ← 실제 사용 파일 ★
    draft_info.json.bak                    ← Timelines 백업
```

## 편집 전 필수 절차

> 지키지 않으면 CapCut이 열릴 때 변경사항을 덮어씀

1. **CapCut 완전 종료 확인**
   ```bash
   ps aux | grep -i capcut | grep -v grep
   # 출력 없어야 함
   ```
2. **4개 파일 모두 동일하게 수정**
   - `draft_info.json` (루트)
   - `draft_info.json.bak` (루트 백업)
   - `Timelines/<UUID>/draft_info.json`
   - `Timelines/<UUID>/draft_info.json.bak`
3. **`.locked` 파일 삭제** — 존재하면 CapCut이 백업에서 복원함
4. CapCut 실행

## 시간 단위

내부 시간 단위: **마이크로초(µs)**. `1초 = 1,000,000`

### 30fps 프레임 정렬 (필수)

CapCut은 모든 타임스탬프를 **30fps 프레임 경계**에 정렬한다.

```python
FPS = 30

def frame_to_us(frame: int) -> int:
    """프레임 번호 → µs (정수 연산, 반올림)"""
    numerator = frame * 1_000_000
    result = numerator // FPS
    if numerator % FPS * 2 >= FPS:
        result += 1
    return result

def snap_to_frame(us: float) -> int:
    """임의 µs → 가장 가까운 프레임 경계 µs"""
    frame = round(us * FPS / 1_000_000)
    return frame_to_us(frame)
```

### ⚠️ target_timerange.start 누적 오차 방지

`timeline_pos += dur_us`로 µs를 누적하면 프레임 경계를 벗어난다.  
**반드시 프레임 번호로 누적하고, µs는 마지막에만 변환**한다.

```python
timeline_frame = 0
for start, end in final_segs:
    start_frame = round(start * FPS)
    end_frame   = round(end   * FPS)
    dur_frame   = end_frame - start_frame

    start_us        = frame_to_us(start_frame)
    dur_us          = frame_to_us(end_frame) - frame_to_us(start_frame)
    timeline_pos_us = frame_to_us(timeline_frame)

    # ... 세그먼트 생성 ...

    timeline_frame += dur_frame  # ← µs 아닌 프레임 단위 누적
```

검증: `(v * 30) % 1_000_000` 값이 0, 10, 20 중 하나여야 한다.

## ⚠️ 핵심: 다중 세그먼트 생성 시 올바른 구조

**내가 처음에 틀렸던 점:** 단일 `material_id`를 모든 세그먼트가 공유하면 CapCut이 올바르게 인식하지 못한다.

**CapCut 실제 방식:** 세그먼트마다 고유한 materials 세트를 생성해야 한다.

### 세그먼트 1개당 생성해야 할 것

```
tracks[0].segments[N]
  material_id → materials.videos[N].id  (세그먼트마다 고유 UUID)
  extra_material_refs[0] → materials.speeds[N].id
  extra_material_refs[1] → materials.placeholder_infos[N].id
  extra_material_refs[2] → materials.canvases[N].id
  extra_material_refs[3] → materials.sound_channel_mappings[N].id
  extra_material_refs[4] → materials.material_colors[N].id
  extra_material_refs[5] → materials.vocal_separations[N].id
```

### 각 material 템플릿

> ⚠️ **사진과 영상의 포맷이 다름** — 잘못된 포맷은 화면이 검정으로 렌더링됨 (실측 확인)
>
> | 필드 | 영상 (`video`) | 사진 (`photo`) |
> |------|---------------|---------------|
> | `type` | `"video"` | `"photo"` |
> | `duration` | 실제 길이 (µs) | `10_800_000_000` (3h 고정값) |
> | `has_audio` | True/False | `False` |
> | `extra_material_refs` | 7개 (loudness 포함) | **6개 (loudness 없음)** |
> | `source_timerange.duration` | 클립 구간 | 표시 시간 (=target) |

```python
PHOTO_DURATION_US = 10_800_000_000   # CapCut 사진 고정값 (3시간) — 표시 시간과 무관

def make_video_material(uid, video_path, video_duration_us, local_material_id):
    """영상 전용. 사진이면 make_photo_material() 사용."""
    return {
        "id": uid, "type": "video",
        "duration": video_duration_us,          # 원본 전체 길이 (변경 금지)
        "path": video_path,
        "media_path": "", "local_id": "",
        "has_audio": True,
        "width": 1920, "height": 1080,
        "category_name": "local",
        "material_name": os.path.basename(video_path),
        "local_material_id": local_material_id,
        "crop": {"upper_left_x":0.0,"upper_left_y":0.0,"upper_right_x":1.0,
                 "upper_right_y":0.0,"lower_left_x":0.0,"lower_left_y":1.0,
                 "lower_right_x":1.0,"lower_right_y":1.0},
        "crop_ratio": "free", "crop_scale": 1.0,
        "source_platform": 0, "check_flag": 62978047,
        # ... (나머지 빈 필드들)
    }

def make_photo_material(uid, photo_path, img_width, img_height):
    """사진(JPEG/PNG 등) 전용.
    - type: "photo" (영상과 다름)
    - duration: PHOTO_DURATION_US = 10_800_000_000 (3h 고정, 실제 표시 시간과 무관)
    - has_audio: False
    - extra_material_refs: 6개 (loudness 없음 — 영상은 7개)
    """
    return {
        "id": uid, "type": "photo",             # "video" 아님
        "duration": PHOTO_DURATION_US,          # 3시간 고정값 (표시 시간과 무관)
        "path": photo_path,
        "media_path": "", "local_id": "",
        "has_audio": False,                     # 사진은 항상 False
        "width": img_width, "height": img_height,  # 실제 이미지 크기
        "material_name": os.path.basename(photo_path),
        "picture_from": "none",
        "crop": {"upper_left_x":0.0,"upper_left_y":0.0,"upper_right_x":1.0,
                 "upper_right_y":0.0,"lower_left_x":0.0,"lower_left_y":1.0,
                 "lower_right_x":1.0,"lower_right_y":1.0},
        "crop_ratio": "free", "crop_scale": 1.0,
        "source_platform": 0, "check_flag": 62978047,
        # ... (나머지 빈 필드들)
    }

# 사진 세그먼트 extra_material_refs: 6개 (loudness 없음)
# photo_extra = [speed_id, placeholder_id, canvas_id, sound_channel_id, material_color_id, vocal_sep_id]
#
# 영상 세그먼트 extra_material_refs: 7개 (loudness 포함)
# video_extra = [speed_id, placeholder_id, canvas_id, sound_channel_id, material_color_id, loudness_id, vocal_sep_id]

def make_speed(uid):
    return {"id": uid, "type": "speed", "mode": 0, "speed": 1.0, "curve_speed": None}

def make_placeholder(uid):
    return {"id": uid, "type": "placeholder_info", "meta_type": "none",
            "res_path": "", "res_text": "", "error_path": "", "error_text": ""}

def make_canvas(uid):
    return {"id": uid, "type": "canvas_color", "color": "", "blur": 0.0,
            "image": "", "album_image": "", "image_id": "", "image_name": "",
            "source_platform": 0, "team_id": ""}

def make_sound_channel(uid):
    return {"id": uid, "type": "none", "audio_channel_mapping": 0, "is_config_open": False}

def make_material_color(uid):
    return {"id": uid, "is_color_clip": False, "is_gradient": False,
            "solid_color": "", "gradient_colors": [], "gradient_percents": [],
            "gradient_angle": 90.0, "width": 0.0, "height": 0.0}

def make_vocal_separation(uid):
    return {"id": uid, "type": "vocal_separation", "choice": 0,
            "removed_sounds": [], "time_range": None,
            "production_path": "", "final_algorithm": "", "enter_from": ""}
```

### 세그먼트 생성 핵심 Python 코드

```python
import uuid, json, os

def new_id():
    return str(uuid.uuid4()).upper()

def build_segments(final_segs, orig_video):
    """
    final_segs: [[start_sec, end_sec], ...] 원본 영상 기준 타임스탬프
    orig_video: 원본 draft에서 읽은 videos[0] (local_material_id 등 보존)
    """
    US = 1_000_000
    VIDEO_DUR = orig_video['duration']
    VIDEO_PATH = orig_video['path']
    LOCAL_MAT_ID = orig_video['local_material_id']

    segments = []
    mat_videos, mat_speeds, mat_placeholders = [], [], []
    mat_canvases, mat_sounds, mat_colors, mat_vocals = [], [], [], []

    timeline_pos = 0
    for start, end in final_segs:
        dur_us = int((end - start) * US)
        start_us = int(start * US)

        vid_id = new_id()
        spd_id = new_id()
        plc_id = new_id()
        cvs_id = new_id()
        snd_id = new_id()
        col_id = new_id()
        vcl_id = new_id()

        # materials 추가
        mat_videos.append(make_video_material(vid_id, VIDEO_PATH, VIDEO_DUR, LOCAL_MAT_ID))
        mat_speeds.append(make_speed(spd_id))
        mat_placeholders.append(make_placeholder(plc_id))
        mat_canvases.append(make_canvas(cvs_id))
        mat_sounds.append(make_sound_channel(snd_id))
        mat_colors.append(make_material_color(col_id))
        mat_vocals.append(make_vocal_separation(vcl_id))

        # 세그먼트
        segments.append({
            "id": new_id(),
            "material_id": vid_id,
            "extra_material_refs": [spd_id, plc_id, cvs_id, snd_id, col_id, vcl_id],
            "source_timerange": {"start": start_us, "duration": dur_us},
            "target_timerange": {"start": timeline_pos, "duration": dur_us},
            "speed": 1.0, "volume": 1.0,
            "clip": {"alpha":1.0,"flip":{"horizontal":False,"vertical":False},
                     "rotation":0.0,"scale":{"x":1.0,"y":1.0},"transform":{"x":0.0,"y":0.0}},
            # ... (나머지 필드들)
        })
        timeline_pos += dur_us

    return segments, {
        "videos": mat_videos, "speeds": mat_speeds,
        "placeholder_infos": mat_placeholders, "canvases": mat_canvases,
        "sound_channel_mappings": mat_sounds, "material_colors": mat_colors,
        "vocal_separations": mat_vocals,
    }
```

## ⚠️ 새 CapCut 프로젝트 생성 — 완전 가이드

### 핵심 원칙 3가지

| # | 원칙 | 위반 시 증상 |
|---|------|-------------|
| 1 | **CapCut 먼저 종료** | 변경사항 무시됨 (런타임에 덮어씀) |
| 2 | **기존 프로젝트를 통째로 복사** | 필수 파일 누락 → 프로젝트 열리지 않음 |
| 3 | **root_meta_info.json에 등록** | 폴더 있어도 CapCut 목록에 보이지 않음 |

---

### 올바른 생성 절차 (5단계)

#### 0. CapCut 종료
```python
import subprocess, time
subprocess.run(["pkill", "-x", "CapCut"])
time.sleep(2)
```

#### 1. 템플릿 프로젝트 통째로 복사
```python
import shutil
PROJECTS = Path.home() / "Movies/CapCut/User Data/Projects/com.lveditor.draft"
shutil.copytree(str(PROJECTS / "0530"), str(PROJECTS / "새프로젝트명"))
```
- JSON을 처음부터 조립하면 `attachment_editing.json`, `draft.extra` 등 수십 개 파일이 누락됨
- 복사 후 `draft_info.json`만 수정

#### 2. Timeline UUID 새로 발급 (필수!)
```python
# 템플릿과 UUID 공유 시 CapCut이 두 프로젝트를 혼동 → 반드시 새 UUID
old_uuid = next(e.name for e in (project_dir/"Timelines").iterdir() if e.is_dir() and "-" in e.name)
new_uuid = str(uuid.uuid4()).upper()
(project_dir/"Timelines"/old_uuid).rename(project_dir/"Timelines"/new_uuid)
```

#### 3. Timelines/project.json 업데이트 (형식 주의)
```python
# ❌ 잘못된 형식 (단순 dict)
{"version": 1, "timeline_id": "..."}

# ✅ 올바른 형식 — 0531(CapCut 직접 생성) 기준으로 검증됨
# 핵심: 외부 id ≠ main_timeline_id, color_space=-1, render_index_track_mode_on=False
outer_id = new_id()  # main_timeline_id와 반드시 다른 UUID
proj_json = {
    "config": {"color_space": -1, "render_index_track_mode_on": False, "use_float_render": False},
    "create_time": now, "id": outer_id,          # 외부 id = 별도 UUID
    "main_timeline_id": timeline_uuid,            # Timelines/ 폴더 이름과 일치
    "timelines": [{"create_time": now, "id": timeline_uuid, "is_marked_delete": False,
                   "name": "타임라인 01", "update_time": now}],
    "update_time": now, "version": 0
}
# project.json + project.json.bak 모두 저장
```

#### 4. draft_info.json 수정 및 4개 파일 저장
```python
import copy
draft["id"] = new_id();  draft["name"] = "새프로젝트명"
draft["duration"] = video_dur_us;  draft["path"] = ""

# text material: deepcopy 후 id/content만 교체 (처음부터 조립 금지)
orig = draft["materials"]["texts"][0]
new_text = copy.deepcopy(orig)
new_text["id"] = new_id()
content = json.loads(orig["content"])
content["text"] = "자막 텍스트"
for st in content.get("styles", []): st["range"] = [0, len("자막 텍스트")]
new_text["content"] = json.dumps(content, ensure_ascii=False)

# 4개 파일 동시 저장
for p in [root/draft_info.json, root/.bak, Timelines/UUID/draft_info.json, Timelines/UUID/.bak]:
    p.write_text(draft_str)
```

#### 4-b. 불필요한 파일 제거 (0531에 없는 파일 = CapCut이 필요 없음)
```python
# 루트에서 제거
for f in ["attachment_editing.json","attachment_pc_common.json","draft.extra",
          "draft_cover.jpg","key_value.json","performance_opt_info.json","template.tmp"]:
    (project_dir / f).unlink(missing_ok=True)
# Timelines/UUID/ 에서도 제거
for f in ["attachment_editing.json","attachment_pc_common.json","draft.extra","draft_cover.jpg"]:
    (tl_dir / f).unlink(missing_ok=True)
```

#### 5. root_meta_info.json 등록
```python
# CapCut은 이 파일의 all_draft_store 목록만 UI에 표시
# 템플릿 항목을 deepcopy해서 필드만 교체 (누락 방지)
with open(ROOT_META) as f: root = json.load(f)
template_entry = next(d for d in root["all_draft_store"] if "0530" in d["draft_fold_path"])
new_entry = copy.deepcopy(template_entry)
new_entry.update({
    "draft_id": project_id, "draft_name": "새프로젝트명",
    "draft_fold_path": str(project_dir),
    "draft_json_file": str(project_dir / "draft_info.json"),
    "tm_draft_create": now_us, "tm_draft_modified": now_us,
    "tm_duration": duration_us,
})
root["all_draft_store"].insert(0, new_entry)  # 맨 앞 = 최신
with open(ROOT_META, "w") as f: json.dump(root, f, ensure_ascii=False, indent=2)
```

#### 5-b. draft_meta_info.json 생성 (0531 기준 검증됨)
```python
# draft_id = 별도 UUID (draft_info.json의 id와 달라도 됨)
# draft_materials에 실제 영상 정보 포함 (없으면 열릴 수 있어도 소스 연결 안 됨)
# draft_new_version = "" (템플릿 복사 시 "164.0.0" 등이 남아있으면 제거)
meta = {
    "draft_id": str(uuid.uuid4()).upper(),  # 독립 UUID
    "draft_name": project_name,
    "draft_fold_path": str(project_dir),
    "draft_new_version": "",               # ← 빈 문자열 필수
    "draft_materials": [
        {"type": 0, "value": [{
            "extra_info": video_path.name, "file_Path": str(video_path),
            "duration": duration_us, "height": 1080, "width": 1920,
            "metetype": "video", "type": 0, ...
        }]},
        {"type": 1, "value": []}, {"type": 2, "value": []},
        {"type": 3, "value": []}, {"type": 6, "value": []}, {"type": 7, "value": []}
    ],
    # ... (나머지 필드는 scripts/add_subtitles.py register_project() 참조)
}
```

---

### ⚠️ 음성-자막 싱크 — 단어 타임스탬프 필수

긴 자막을 시간 균등 분할하면 빨리 말한 부분과 느리게 말한 부분의 싱크가 어긋난다.  
**Whisper의 단어별 타임스탬프 (`word_timestamps=True`)** 를 활용해 실제 발화 시점에 맞춰 분리.

```python
# Whisper 실행 시
segs, _ = model.transcribe(video, language="ko", beam_size=1, word_timestamps=True)
# 각 segment에 .words 배열 포함: [{start, end, word}, ...]
```

**파일 구조:**
```
before.srt              ← Whisper 원본 (텍스트)
before_words.json       ← 단어 타임스탬프 캐시 (필수!)
before_verified.srt     ← subtitle-verifier 검증 결과
```

**워크플로우 (4단계):**

1. `transcribe()` — SRT + `before_words.json` 동시 저장 (단어 타임스탬프 포함)
2. `subtitle-verifier` 에이전트 — 검증된 SRT 생성 (텍스트만 수정)
3. `merge_verified_text_with_words()` — **단어 순차 매칭**으로 시간 정보 결합
4. `split_with_word_sync()` + `remove_overlaps()` — 18자 분리 + 겹침 제거

#### 3단계: 단어 순차 매칭 (중요!)

❌ **시간 범위 기반 매칭의 문제점:**
- `[vs.start - 0.2, vs.end + 0.2]`로 겹치는 단어 추출 시
- 같은 단어가 인접한 두 자막에 **중복 할당**됨
- 결과: "이렇게 AI 기능을..."이 0.33초만 표시되는 등 자막 순서 꼬임 발생

✅ **순차 매칭 알고리즘 (현재 적용):**

```python
def merge_verified_text_with_words(verified_segs, word_segs):
    # 1. 모든 단어를 시간 순서 큐로 평탄화
    all_words = [w for ws in word_segs for w in ws.get("words", [])]

    word_idx = 0  # 큐 진행 인덱스
    merged = []

    for vs_i, vs in enumerate(verified_segs):
        verified_words = vs["text"].split()
        n_need = len(verified_words)

        # 2. 다음 자막의 시작 시간 확인
        next_vs_start = None
        for next_vs in verified_segs[vs_i + 1:]:
            if next_vs.get("text", "").strip():
                next_vs_start = next_vs["start"]
                break

        # 3. 큐에서 단어 순차 소비 (다음 자막 시간 넘어가면 중단)
        take_indices = []
        i = word_idx
        while i < len(all_words) and len(take_indices) < n_need:
            w = all_words[i]
            if next_vs_start is not None and w["start"] >= next_vs_start and take_indices:
                break  # 다음 자막용으로 남김
            take_indices.append(i)
            i += 1

        taken = [all_words[idx] for idx in take_indices]
        word_idx = take_indices[-1] + 1  # 큐 진행

        # 4. 검증 단어 수 ↔ 가져온 단어 수가 다르면 비율 매칭
        # ... (slot[lo:hi] 분배)
```

**핵심 보장:**
- 각 단어는 정확히 **한 자막에만** 할당됨
- 자막 순서는 검증 SRT 원본 순서 유지
- 시간은 실제 발화 시점에 정확히 동기화

#### 4단계 (보조): 짧은 종결어미 머지

`split_with_word_sync` 후처리. "됩니다.", "되겠죠", "거예요." 같은 짧은 종결어미가 단독 자막으로 분리되면 시각적으로 어색하므로 **앞 파트에 머지**한다.

```python
_SHORT_TAIL_ENDINGS = (
    "다", "다.", "요", "요.", "죠", "죠.", "네", "네.", "까", "까?",
    "함", "함.", "음", "음.", "임", "임.", "지", "지.", "요!", "다!",
)

def _merge_short_tails(parts, max_chars=18, min_tail_chars=5, max_merged_chars=27):
    """파트가 짧거나(≤5자) 종결어미로 끝나면(≤8자) 앞 파트에 머지.
    단, 머지 후 길이가 27자(=max_chars*1.5)를 초과하면 머지 안 함."""
    merged = [parts[0]]
    for part in parts[1:]:
        prev = merged[-1]
        is_short = len(part["text"]) <= min_tail_chars
        is_ending = part["text"].endswith(_SHORT_TAIL_ENDINGS) and len(part["text"]) <= 8
        merged_len = len(prev["text"]) + 1 + len(part["text"])
        if (is_short or is_ending) and merged_len <= max_merged_chars:
            prev["text"] = prev["text"] + " " + part["text"]
            prev["end"] = part["end"]
        else:
            merged.append(part)
    return merged
```

**효과:**
- "결제해가지고 연동을 시켜줘야" + "됩니다." → "결제해가지고 연동을 시켜줘야 됩니다." (19자)
- "Gemini의 API 키를 연결하면" + "되겠죠" → "Gemini의 API 키를 연결하면 되겠죠" (24자)

**가독성 보호:** 머지 후 27자 초과면 머지하지 않음 (한 줄 표시 한계 유지).

#### 5단계: 겹침 제거

분리 후 인접 자막이 미세하게 겹칠 수 있어 후처리 필수:

```python
def remove_overlaps(parts, min_gap=0.02):
    sorted_parts = sorted(parts, key=lambda p: p["start"])
    for i in range(len(sorted_parts) - 1):
        next_start = sorted_parts[i + 1]["start"]
        if sorted_parts[i]["end"] > next_start - min_gap:
            sorted_parts[i]["end"] = max(sorted_parts[i]["start"] + 0.1,
                                          next_start - min_gap)
    return sorted_parts
```

**주의:**
- Whisper `medium` + `word_timestamps=True`는 5분 영상에 20분+ 소요 → `small` + `beam_size=1` 사용 (3분)
- `before_words.json`이 없으면 자동으로 균등 분할 폴백 (싱크 부정확)
- 검증 SRT의 자막 개수가 단어 타임스탬프 구간 개수와 달라도 OK (순차 매칭으로 흡수)

---

### ⚠️ 자막 가독성 — 검은 외곽선 필수

흰색 자막은 **흰 배경 / 밝은 배경 / 코드 에디터 화면**에서 잘 보이지 않음.  
검은 외곽선(stroke)을 추가하면 어떤 배경에서도 잘 보임 (영화/YouTube 표준 방식).

**두 위치 동시 설정 필요:**
1. `material.border_*`: CapCut UI 메타데이터
2. `content.styles[].strokes`: 실제 영상 렌더링용 (이게 없으면 외곽선이 그려지지 않음)

```python
def apply_subtitle_outline(text_material, border_width=0.15):
    # 1. material 레벨
    text_material["border_color"] = "#000000"
    text_material["border_width"] = border_width
    text_material["border_alpha"] = 1.0
    text_material["border_mode"] = 0

    # 2. content.styles[] 레벨 — 실제 렌더링
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
```

**권장 두께:**

| 값 | 효과 |
|---|------|
| 0.08 | 매우 얇음 (흰 배경에서 안 보임) ❌ |
| 0.10 | 보통 |
| **0.15** | **권장 (균형)** ✓ |
| 0.20 | 두꺼움 (작은 자막에 추천) |

**⚠️ 주의:** `material.border_*`만 설정하고 `content.strokes`를 누락하면 CapCut UI에서는 외곽선이 켜진 것처럼 보이지만 **실제 영상에는 렌더링되지 않음**. 둘 다 필수.

---

### 페이드 인 / 페이드 아웃 애니메이션 적용

세그먼트에 페이드 인/아웃 효과를 주려면 **`material_animations`** 에 `sticker_animation` 타입을 추가하고, 세그먼트의 `extra_material_refs` 에 그 ID를 등록한다.

#### CapCut 기본 페이드 리소스 ID (실측)

| 효과 | resource_id | category |
|------|-------------|----------|
| 페이드 인 | `6798320778182922760` | Trending1 (`2037708298`) |
| 페이드 아웃 | `6798320902548230669` | Trending-2 (`2037708370`) |

> ⚠️ 두 효과는 **resource_id가 다름**. 같은 ID로 type만 `"in"/"out"` 바꿔 적용하면 CapCut이 둘 다 페이드 아웃으로 렌더링한다 (실측 확인).

#### 코드 예시

```python
FADE_IN_ID    = "6798320778182922760"
FADE_OUT_ID   = "6798320902548230669"
FADE_IN_PATH  = "~/Library/Containers/com.lemon.lvoverseas/Data/Movies/CapCut/User Data/Cache/effect/6798320778182922760/883ad04bd79b502aaa55b5d9b87175ea"
FADE_OUT_PATH = "~/Library/Containers/com.lemon.lvoverseas/Data/Movies/CapCut/User Data/Cache/effect/6798320902548230669/c6f05ce62355b537be762550040bfc08"
FADE_DUR_US   = 800_000   # 0.8초 권장

def make_fade_animation(seg_dur_us, fade_dur_us=FADE_DUR_US):
    """fade-in + fade-out 동시 적용용 material_animation 생성.

    짧은 슬라이드 (fade_dur*3 미만)는 fade를 1/3로 축소 권장."""
    actual = min(fade_dur_us, seg_dur_us // 3)
    out_start = max(0, seg_dur_us - actual)
    return {
        "id": new_id(),
        "type": "sticker_animation",
        "animations": [
            {
                "id": FADE_IN_ID,
                "type": "in",
                "start": 0,
                "duration": actual,
                "path": os.path.expanduser(FADE_IN_PATH),
                "platform": "all",
                "resource_id": FADE_IN_ID,
                "third_resource_id": FADE_IN_ID,
                "source_platform": 1,
                "name": "페이드 인",
                "category_id": "2037708298",
                "category_name": "Trending1",
                "panel": "video",
                "material_type": "video",
                "anim_adjust_params": None,
                "request_id": ""
            },
            {
                "id": FADE_OUT_ID,
                "type": "out",
                "start": out_start,
                "duration": actual,
                "path": os.path.expanduser(FADE_OUT_PATH),
                "platform": "all",
                "resource_id": FADE_OUT_ID,
                "third_resource_id": FADE_OUT_ID,
                "source_platform": 1,
                "name": "페이드 아웃",
                "category_id": "2037708370",
                "category_name": "Trending-2",
                "panel": "video",
                "material_type": "video",
                "anim_adjust_params": None,
                "request_id": ""
            }
        ],
        "multi_language_current": "none"
    }

# 적용
anim_mat = make_fade_animation(seg["target_timerange"]["duration"])
draft["materials"]["material_animations"].append(anim_mat)
seg["extra_material_refs"].append(anim_mat["id"])
```

#### 핵심 규칙

- **start/duration 단위는 마이크로초(µs)** — `seg.target_timerange.duration` 과 동일 단위
- `animations[i].start` 는 **세그먼트 내부 기준 오프셋** (0 = 세그먼트 시작)
- 페이드 아웃 `start = seg_duration - fade_duration` 으로 끝에 맞춤
- 짧은 슬라이드(예: 2초)에 0.8초 fade를 양쪽 적용하면 페이드만 보이므로 **1/3 축소** 권장
- 페이드 인만 / 아웃만 적용하려면 `animations` 배열에 하나만 넣으면 됨

---

### ⚠️ 자막 잘림 방지 — 세그먼트 분리

한국어 자막은 한 줄에 **18글자 초과** 시 화면 오른쪽이 잘림.  
`\n` 개행이 아니라 **별도 세그먼트로 분리**한다.

```
"이번 영상에서는 별도의 AI API 결제 없이 구독 중인 Codex만으로"
  → seg1: "이번 영상에서는 별도의 AI"   (0.00 → 2.04s)
  → seg2: "API 결제 없이 구독 중인"      (2.06 → 4.52s)
  → seg3: "Codex만으로"                 (4.54 → 5.56s)
```

#### 단어 동기화 분리 (`split_with_word_sync` 권장)

`merge_verified_text_with_words`로 단어 타임스탬프가 부여된 자막을 분리할 때 사용:

```python
def split_with_word_sync(seg: dict, max_chars: int = 18) -> list[dict]:
    """words 배열을 따라가며 max_chars 이내에서 끊음.
    각 파트의 start/end = 실제 단어들의 시작/끝 시간."""
    words = seg.get("words", [])
    if not words:
        # 폴백: 시간 균등 분할
        return [...]

    parts = []
    current_words = []
    current_len = 0
    for w in words:
        word_len = len(w["word"].strip())
        candidate_len = current_len + (1 if current_words else 0) + word_len
        if candidate_len <= max_chars or not current_words:
            current_words.append(w)
            current_len = candidate_len
        else:
            parts.append(current_words)
            current_words = [w]
            current_len = word_len
    if current_words:
        parts.append(current_words)

    return [{
        "start": grp[0]["start"],
        "end": grp[-1]["end"],
        "text": "".join(w["word"] for w in grp).strip(),
    } for grp in parts]
```

#### 폴백: 균등 분할 (`split_subtitle`)

단어 타임스탬프가 없는 경우에만 사용. 빠른 발화 부분에서 싱크가 어긋날 수 있음:

```python
def split_subtitle(text: str, max_chars: int = 18) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    words = text.split(" ")
    parts, current = [], ""
    for word in words:
        candidate = (current + " " + word).strip() if current else word
        if len(candidate) <= max_chars:
            current = candidate
        else:
            if current: parts.append(current)
            while len(word) > max_chars:
                parts.append(word[:max_chars]); word = word[max_chars:]
            current = word
    if current: parts.append(current)
    return parts if parts else [text]

# 시간 균등 분할
parts = split_subtitle(seg["text"])
part_dur = total_dur // len(parts)
for j, text in enumerate(parts):
    cursor = total_start + part_dur * j
    dur = part_dur if j < len(parts)-1 else (total_end - cursor)
    # → 각각 별도 material + segment 생성
```

- `max_chars=18` : 한국어 기준 검증값 (영어는 30~35 권장)
- `styles[].range` = `[0, len(text)]` 각 파트 길이 기준으로 개별 설정

---

### 자막 자동 워크플로우 (capcut 에이전트가 오케스트레이션)

**사용자가 "자막 올려줘" / "자막 추가해줘" 라고 요청하면 다음 단계를 자동 실행한다:**

#### 🛑 Human-in-the-Loop: Whisper 모델 선택 (필수)

**Whisper 전사를 시작하기 전에 반드시 사용자에게 모델을 물어본다.**
- 이미 `<video>.srt`가 존재하면 모델 선택 단계 생략 (캐시 재사용)
- 이미 `<video>_verified.srt`가 존재하면 모델 선택 + Whisper 모두 생략

**`AskUserQuestion` 도구로 선택지 제공:**

```python
AskUserQuestion(questions=[{
    "question": "Whisper 모델을 선택해주세요 (영상 길이에 따라 시간이 다름)",
    "header": "Whisper 모델",
    "multiSelect": False,
    "options": [
        {
            "label": "small (Recommended)",
            "description": "5분 영상 기준 ~3분 소요. 균형 잡힌 정확도. 대부분의 경우 권장."
        },
        {
            "label": "tiny",
            "description": "~30초. 가장 빠르지만 한국어 정확도가 낮음. 빠른 미리보기용."
        },
        {
            "label": "base",
            "description": "~1분. 보통 수준. small이 안 되는 환경의 대안."
        },
        {
            "label": "medium",
            "description": "~10-20분. 매우 정확하지만 시간이 오래 걸림. 중요한 영상에 권장."
        },
        {
            "label": "large-v3",
            "description": "~30-60분. 최고 정확도. 인터뷰/강의처럼 정확도가 최우선인 경우."
        }
    ]
}])
```

**선택된 모델을 `--model` 플래그로 전달:**
```bash
uv run scripts/add_subtitles.py <video.mov> --model <선택된모델> --no-verify
```

**선택 가이드:**

| 영상 종류 | 권장 모델 | 이유 |
|----------|----------|------|
| 빠른 미리보기 | tiny / base | 즉시 확인 |
| 일반 콘텐츠 (YouTube 등) | **small** | 시간/정확도 균형 |
| IT/기술 용어 多 | medium 또는 small + 사전 누적 | 전문 용어 정확도 |
| 강의/인터뷰 (중요) | medium / large-v3 | 한 번에 정확하게 |
| 한국어 + 영어 혼합 | small 이상 | 다국어 처리 |

⚠️ **시간이 오래 걸리는 모델(medium/large-v3) 선택 시 사용자에게 예상 시간 안내**:
- "medium 모델은 5분 영상 기준 약 15분 소요됩니다. 진행하시겠어요?"

#### 누적 오인식 사전 시스템 (`corrections.json`)

영상마다 발견된 한국어 오인식을 사전에 누적 저장 → 다음 영상에서 자동 적용.
- `apply_corrections_dictionary()`: SRT 로드 직후 1차 적용 (Python에서 자동 처리)
- `subtitle-verifier`: 사전에 없는 새 패턴만 발견하고 사전에 추가
- 영상이 늘수록 LLM 호출 시간/비용 감소, 정확도 증가

```
Whisper SRT → [사전 1차 적용] → [LLM 검증] → 검증된 SRT
                  ↑                ↓
              corrections.json ←—새 패턴 추가
```


#### 1단계: Whisper로 자막 생성

```bash
uv run scripts/add_subtitles.py <video.mov> --no-verify
```

- `<video>.srt` 파일이 생성된다 (이미 있으면 재사용)
- `--no-verify`로 안내 메시지 생략
- **CapCut 프로젝트도 함께 생성되지만, 검증 후 재생성하므로 이건 임시본**

#### 2단계: subtitle-verifier 에이전트 호출 (필수)

```python
Task(
    description="자막 오타 검증",
    subagent_type="subtitle-verifier",
    prompt=f"<video>.srt 파일을 검증하고 <video>_verified.srt로 저장해줘. "
           f"영상 주제는 [컨텍스트 기반]이야."
)
```

- subtitle-verifier가 한국어 오인식·맞춤법을 교정
- `<video>_verified.srt` 파일 생성
- 검증 결과(교정 건수, 주요 변경 사항) 사용자에게 보고

#### 3단계: 검증된 자막으로 CapCut 프로젝트 재생성

```bash
uv run scripts/add_subtitles.py <video.mov> --srt <video>_verified.srt
```

- 기존 프로젝트 폴더는 자동으로 덮어씌워짐
- 검증된 자막 + 분리 처리(18자 초과 시) + 30fps 정렬 모두 적용

#### 자동 실행 의사결정

| 상황 | 동작 |
|------|------|
| `<video>_verified.srt` 이미 존재 | 2단계 생략, 바로 3단계 |
| `<video>.srt`만 존재 | 1단계 생략, 2→3단계 |
| 둘 다 없음 | 1→2→3 전체 실행 |
| 사용자가 명시적으로 "검증 없이" 요청 | 1단계만 실행 (`--no-verify`) |

#### 사용자 보고 형식

각 단계 완료 시 진행 상황을 한 줄로 보고:

```
[1/3] Whisper 전사 완료 — 83개 자막
[2/3] 검증 완료 — 12건 교정 ("챕포"→"챗봇" 외)
[3/3] CapCut 프로젝트 생성 — /Users/.../before
✓ CapCut을 열어 'before' 프로젝트를 확인하세요
```

검증 에이전트: `.claude/agents/subtitle-verifier/AGENT.md`

### 자막 추가 스크립트

`scripts/add_subtitles.py` — 위 5단계를 모두 자동 처리

```bash
# 기본 (small 모델, 권장)
uv run scripts/add_subtitles.py video.mov

# 모델 선택 (human-in-the-loop으로 사용자가 선택)
uv run scripts/add_subtitles.py video.mov --model medium
uv run scripts/add_subtitles.py video.mov --model large-v3 --beam-size 5

# SRT 있으면 Whisper 생략 (또는 자동으로 .srt 캐시 활용)
uv run scripts/add_subtitles.py video.mov --srt video.srt

# 프로젝트 이름 지정
uv run scripts/add_subtitles.py video.mov --project-name my_project
```

| 항목 | 값 |
|------|----|
| Python 실행환경 | `uv run` (의존성 자동 관리, faster-whisper 포함) |
| Whisper 입력 | 영상 직접 X → **16kHz mono WAV 자동 추출 후 전사** ({stem}_audio.wav 캐시) |
| Whisper 모델 | `--model {tiny|base|small|medium|large-v3}` (기본: small, 사용자 선택) |
| beam_size | `--beam-size N` (기본 1, large-v3는 5 권장) |
| 템플릿 프로젝트 | `CAPCUT_PROJECTS/0530` (스크립트 상단 `TEMPLATE_NAME` 상수) |

---

## 편집 가능 항목

| 항목 | JSON 경로 |
|------|-----------|
| 컷 (트리밍) | `tracks[0].segments[N].source_timerange` / `target_timerange` |
| 재생 속도 | `tracks[0].segments[N].speed` + `materials.speeds[N].speed` |
| 볼륨 | `tracks[0].segments[N].volume` |
| 회전 | `tracks[0].segments[N].clip.rotation` |
| 크기 | `tracks[0].segments[N].clip.scale.x/y` |
| 위치 | `tracks[0].segments[N].clip.transform.x/y` |
| 좌우반전 | `tracks[0].segments[N].clip.flip.horizontal` |

## 자동 편집 스크립트

`scripts/capcut_editor.py` — 위 구조를 올바르게 구현한 완성 스크립트.

```bash
# CapCut 종료 후 실행
python3 scripts/capcut_editor.py /tmp/final_segments.json

# 무음 제거만 (NG 제거 없이)
python3 scripts/capcut_editor.py /tmp/speech_segments.json

# 프로젝트 경로 직접 지정
python3 scripts/capcut_editor.py /tmp/final_segments.json \
  --project "~/Movies/CapCut/User Data/Projects/com.lveditor.draft/새프로젝트"
```

- `segments.json` 형식: `[[start_sec, end_sec], ...]`
- 4개 파일 동시 저장 + `.locked` 삭제 자동 처리
- CapCut 실행 중이면 자동 감지 후 종료

## 컷편집 전체 파이프라인

```bash
SCRIPTS="/Users/seungryk/youtube/vibecut/scripts"

# 1. 무음 구간 감지 (-35dB 이하, 0.5초 이상)
ffmpeg -i input.mp4 -af silencedetect=noise=-35dB:d=0.5 -f null - 2>&1 \
  | grep -E "silence_(start|end)" > /tmp/silence_data.txt

# 2. 발화 구간 계산 → /tmp/speech_segments.json
#    (silence_data.txt 파싱, MIN_SPEECH=0.3초, PAD=0.05초)

# 3. NG 자동 감지 (오디오 자동 추출 → Whisper 전사 → 패턴 분석)
#    _words.json 캐시 있으면 전사 생략, _audio.wav 캐시 있으면 추출 생략
uv run "${SCRIPTS}/detect_ng.py" input.mp4 \
  --speech /tmp/speech_segments.json \
  --out /tmp/ng_log.json

# 4. 편집 구간 생성 (NG 필터 포함)
uv run "${SCRIPTS}/make_segments.py" \
  --speech /tmp/speech_segments.json \
  --ng /tmp/ng_log.json \
  --out /tmp/final_segments.json

# 5. CapCut JSON 적용
uv run "${SCRIPTS}/capcut_editor.py" /tmp/final_segments.json
```

**오디오 캐시 전략:**
- `{stem}_audio.wav` — 영상에서 추출한 전체 오디오 (16kHz mono). ffmpeg 추출 1회 후 재사용.
- `{stem}_words.json` — Whisper 전사 결과. detect_ng / add_subtitles 공유 캐시.
- `{stem}_edited_audio.wav` — 편집 구간만 이어붙인 오디오 (--segments 모드 시).

### make_segments.py 파라미터 (사용자 편집 기준 반영)

| 파라미터 | 기존 | 개선 | 근거 |
|----------|------|------|------|
| NG 제거 임계값 | 0% (있으면 제거) | **50%** (절반 이상만 제거) | 사용자는 NG 34% 살림 |
| 갭 병합 간격 | 0.2초 | **0.5초** | 사용자 덩어리 수: 216개 (내것 326개) |
| 최소 발화 길이 | 0.3초 | 0.3초 | 유지 |

### 무음 감지 기본값

| 파라미터 | 값 |
|----------|----|
| `noise` | `-35dB` |
| `d` | `0.5초` |
| `MIN_SPEECH` | `0.3초` |
| `PAD` | `0.05초` |
