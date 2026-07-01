---
name: vibecut-add-subtitles
version: 0.5.0
description: |
  영상 또는 기존 CapCut 프로젝트에 한국어 자막을 자동 생성·적용.
  Whisper 전사 → 문법 경계 분할 → CapCut JSON 적용.
  트리거: "자막 추가", "자막 올려줘", "자막 만들어", "/vibecut-add-subtitles"
metadata:
  category: video
  locale: ko-KR
allowed-tools:
  - Bash
  - Read
  - Write
  - AskUserQuestion
  - Agent
---

# vibecut-add-subtitles 스킬

## 모드 선택

| 상황 | 모드 |
|------|------|
| 원본 영상에서 처음 자막 생성 (새 CapCut 프로젝트) | **모드 A** |
| 이미 편집된 CapCut 프로젝트에 자막 추가 | **모드 B** |

---

## 모드 A: 원본 영상 기준 (새 프로젝트 생성)

```
영상
  ├─ [0] Whisper 모델 선택
  ├─ [1] add_subtitles.py --no-verify --dump-only → subtitle_input.json
  ├─ [2] subtitle-splitter 에이전트 → subtitle_splits.json
  └─ [3] add_subtitles.py --splits → 새 CapCut 프로젝트 생성
```

### 전제 조건

```bash
if pgrep -i "CapCut" > /dev/null 2>&1; then pkill -i "CapCut" && sleep 2; fi

VIBECUT_CONFIG="${HOME}/.vibecut/config.json"
SCRIPTS=""
if [ -f "${VIBECUT_CONFIG}" ]; then
  SCRIPTS=$(python3 -c "import json; print(json.load(open('${VIBECUT_CONFIG}')).get('scripts_dir',''))" 2>/dev/null)
fi
if [ -z "${SCRIPTS}" ]; then
  SCRIPTS=$(find "${HOME}/.claude/plugins/cache/vibecut" -name "capcut_editor.py" -maxdepth 8 2>/dev/null | head -1 | xargs dirname 2>/dev/null)
fi
[ -z "${SCRIPTS}" ] && echo "❌ vibecut-setup 먼저 실행" && exit 1
```

### Whisper 모델 선택 (캐시 없을 때만)

`{stem}_words.json` 또는 `{stem}.srt`가 있으면 건너뜁니다.

```python
AskUserQuestion(questions=[{
    "question": "Whisper 모델을 선택해주세요.",
    "header": "Whisper 모델",
    "multiSelect": False,
    "options": [
        {"label": "small (Recommended)", "description": "5분 ~2분. 한국어 정확도 높음. 대부분 권장."},
        {"label": "large-v3-turbo", "description": "~5분. large-v3 대비 6× 빠르고 정확도 유사."},
        {"label": "large-v3", "description": "~30분+. 최고 범용 정확도."},
    ]
}])
```

⚠ 커뮤니티 한국어 fine-tune 모델을 기본 옵션으로 제시하지 않습니다 — 실전에서
검증 없이 신뢰했다가 긴 오디오 대부분을 누락한 사례가 있습니다. 자세한 배경과
안전하게 사용하는 절차는 `vibecut-auto-edit` 스킬의 동일 섹션을 참고하세요.

### 실행

```bash
VIDEO="<영상 파일 경로>"

# 전사 + subtitle_input.json 생성
uv run "${SCRIPTS}/add_subtitles.py" "${VIDEO}" \
  --model "${WHISPER_MODEL}" --no-verify --dump-only
```

subtitle-splitter 에이전트 호출:
```
/tmp/subtitle_input.json을 읽어 한국어 문법 경계로 분할하고 /tmp/subtitle_splits.json에 저장해줘.
세그먼트 수 1:1 대응 필수.
```

```bash
# CapCut 프로젝트 생성
uv run "${SCRIPTS}/add_subtitles.py" "${VIDEO}" \
  --srt "${VIDEO%.*}.srt" \
  --no-verify \
  --splits /tmp/subtitle_splits.json
```

---

## 모드 B: 기존 CapCut 프로젝트에 자막 추가

수동으로 편집한 프로젝트에 자막을 추가하는 경우.
**비디오 트랙을 건드리지 않고 자막 트랙만 추가합니다.**

```
CapCut draft_info.json
  ├─ [1] draft_info.json 파싱 → 소스 파일·구간 추출
  ├─ [2] ffconcat → {PROJECT}_edited.wav (★ 이 타임스탬프 = CapCut 타임라인)
  ├─ [3] Whisper 전사 → {PROJECT}_edited_words.json
  ├─ [4] words.json → SRT → add_subtitles.py --dump-only → subtitle_input.json
  ├─ [5] subtitle-splitter 에이전트 → subtitle_splits.json
  └─ [6] Python: 비디오 트랙 유지 + 자막 트랙만 추가 → 4개 JSON 갱신
```

### 단계 1: 세그먼트 파악

```bash
PROJECT="<CapCut 프로젝트 경로>"
# 예: /Users/yourname/Movies/CapCut/User Data/Projects/com.lveditor.draft/0628
```

```python
import json, pathlib

proj_dir = pathlib.Path(PROJECT)
data = json.loads((proj_dir / "draft_info.json").read_text())
mats = {v["id"]: v["path"] for v in data["materials"]["videos"]}

for track in data["tracks"]:
    if track["type"] == "video":
        segs = track["segments"]
        paths = set(mats.get(s["material_id"], "?") for s in segs)
        print(f"총 세그먼트: {len(segs)}개")
        for path in sorted(paths):
            count = sum(1 for s in segs if mats.get(s["material_id"]) == path)
            total = sum(s["source_timerange"]["duration"]
                        for s in segs if mats.get(s["material_id"]) == path) / 1e6
            print(f"  {pathlib.Path(path).name}: {count}개, {total:.1f}초")
        break
```

### 단계 2: ffconcat 생성 + 편집 오디오 추출

```python
import json, pathlib

proj_dir = pathlib.Path(PROJECT)
data = json.loads((proj_dir / "draft_info.json").read_text())
mats = {v["id"]: v["path"] for v in data["materials"]["videos"]}

for track in data["tracks"]:
    if track["type"] == "video":
        segs = sorted(track["segments"], key=lambda s: s["target_timerange"]["start"])
        break

lines = ["ffconcat version 1.0"]
for seg in segs:
    src = seg["source_timerange"]
    lines.append(f"file '{mats.get(seg['material_id'], '')}'")
    lines.append(f"inpoint {src['start']/1e6:.6f}")
    lines.append(f"outpoint {(src['start']+src['duration'])/1e6:.6f}")

PROJECT_NAME = pathlib.Path(PROJECT).name
concat_path = f"/tmp/{PROJECT_NAME}_concat.txt"
open(concat_path, "w").write("\n".join(lines))
print(f"ffconcat 저장: {concat_path}")
```

```bash
PROJECT_NAME="<프로젝트명>"  # 예: 0628

ffmpeg -y -f concat -safe 0 -i /tmp/${PROJECT_NAME}_concat.txt \
  -vn -acodec pcm_s16le -ar 16000 /tmp/${PROJECT_NAME}_edited.wav
```

### 단계 3: Whisper 전사

`/tmp/{PROJECT_NAME}_edited_words.json`이 있으면 건너뜁니다.

```python
AskUserQuestion(...)  # 모델 선택 (캐시 없을 때만)
```

```bash
uv run "${SCRIPTS}/detect_ng.py" /tmp/${PROJECT_NAME}_edited.wav \
  --model "${WHISPER_MODEL}" \
  --out /tmp/_ng_unused.json
# → /tmp/{PROJECT_NAME}_edited_words.json 자동 저장
```

### 단계 4: SRT 생성 + subtitle_input.json dump

word-split으로 세분화 후 SRT 생성 → 문법 경계 분할기가 더 깔끔한 입력을 받습니다.

```python
import json

PROJECT_NAME = "<프로젝트명>"
segs = json.load(open(f"/tmp/{PROJECT_NAME}_edited_words.json", encoding="utf-8"))

def split_seg(seg):
    words = [w for w in seg.get('words', []) if 'start' in w and 'end' in w]
    if len(words) < 3:
        return [{'start': seg['start'], 'end': seg['end'], 'text': seg['text'].strip()}]
    durs = [w['end'] - w['start'] for w in words]
    sorted_d = sorted(durs)
    trimmed = sorted_d[:max(1, int(len(sorted_d) * 0.7))]
    mean_d = sum(trimmed) / len(trimmed)
    dur_thr = max(2.5, mean_d * 3.0)
    cut_after = set()
    for i in range(len(words) - 1):
        if durs[i] > dur_thr or words[i+1]['start'] - words[i]['end'] > 1.5:
            cut_after.add(i)
    if not cut_after:
        return [{'start': seg['start'], 'end': seg['end'], 'text': seg['text'].strip()}]
    result, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        if i in cut_after:
            if cur[-1]['end'] - cur[0]['start'] >= 0.3:
                result.append({'start': cur[0]['start'], 'end': cur[-1]['end'],
                               'text': ''.join(x['word'] for x in cur).strip()})
            cur = []
    if cur and cur[-1]['end'] - cur[0]['start'] >= 0.3:
        result.append({'start': cur[0]['start'], 'end': cur[-1]['end'],
                       'text': ''.join(x['word'] for x in cur).strip()})
    return result if result else [{'start': seg['start'], 'end': seg['end'], 'text': seg['text'].strip()}]

sub_segs = []
for seg in segs:
    sub_segs.extend(split_seg(seg))

def fmt(t):
    h=int(t//3600); m=int((t%3600)//60); sec=int(t%60); ms=int((t%1)*1000)
    return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"

srt_lines = []
for i, seg in enumerate(sub_segs, 1):
    srt_lines.append(f"{i}\n{fmt(seg['start'])} --> {fmt(seg['end'])}\n{seg['text']}\n")

open(f"/tmp/{PROJECT_NAME}_edited.srt", "w", encoding="utf-8").write("\n".join(srt_lines))
print(f"SRT 생성: 원본 {len(segs)}개 → word-split 후 {len(sub_segs)}개 세그먼트")
```

```bash
# uv run 필수 (Python 3.10+ 필요)
cd "${SCRIPTS}" && uv run python3 -c "
import sys; sys.path.insert(0,'${SCRIPTS}')
" && uv run "${SCRIPTS}/add_subtitles.py" \
  /tmp/${PROJECT_NAME}_edited.wav \
  --srt /tmp/${PROJECT_NAME}_edited.srt \
  --no-verify --dump-only
# → /tmp/subtitle_input.json
```

### 단계 5: subtitle-splitter 에이전트

```
/tmp/subtitle_input.json을 읽어 한국어 문법 경계로 분할하고
/tmp/subtitle_splits.json에 저장해줘. 세그먼트 수 1:1 대응 필수.
```

### 단계 6: 자막 트랙 추가 (비디오 트랙 유지)

`uv run python3`으로 실행 — `python3` 직접 호출 시 Python 버전 문제 발생.

```python
# /tmp/apply_subtitles.py 로 저장 후 uv run python3 /tmp/apply_subtitles.py 실행
import sys, copy, json, shutil, pathlib

SCRIPTS = "<scripts 경로>"
sys.path.insert(0, SCRIPTS)
from add_subtitles import (
    DEFAULT_TEXT_MATERIAL, DEFAULT_TEXT_SEGMENT,
    new_id, snap_to_frame, remove_overlaps,
    split_by_splits, apply_subtitle_outline,
)

PROJECT = "<CapCut 프로젝트 경로>"
proj_dir = pathlib.Path(PROJECT)
tl_dir = next(d for d in (proj_dir / "Timelines").iterdir() if d.is_dir())

# 백업 (.subtitle_bak)
for p in [proj_dir/"draft_info.json", proj_dir/"draft_info.json.bak",
          tl_dir/"draft_info.json", tl_dir/"draft_info.json.bak"]:
    shutil.copy2(p, str(p) + ".subtitle_bak")

draft = json.loads((proj_dir / "draft_info.json").read_text(encoding="utf-8"))
segments = json.load(open("/tmp/subtitle_input.json", encoding="utf-8"))
splits_data = json.load(open("/tmp/subtitle_splits.json", encoding="utf-8"))

# subtitle_input.json에 top-level start/end 없으면 words에서 파생
for seg in segments:
    if "start" not in seg and seg.get("words"):
        seg["start"] = seg["words"][0]["start"]
        seg["end"] = seg["words"][-1]["end"]

orig_text = copy.deepcopy(DEFAULT_TEXT_MATERIAL)
orig_tseg = copy.deepcopy(DEFAULT_TEXT_SEGMENT)
all_parts = split_by_splits(segments, splits_data)
all_parts = remove_overlaps(all_parts, min_gap=0.02)

text_materials, text_segments = [], []
for render_idx, part in enumerate(all_parts):
    text = part["text"]
    start_us = snap_to_frame(part["start"])
    end_us   = snap_to_frame(part["end"])
    dur_us   = end_us - start_us
    if dur_us <= 0:
        continue

    mat_id = new_id()
    new_text = copy.deepcopy(orig_text)
    new_text["id"] = mat_id
    content_obj = json.loads(orig_text["content"])
    content_obj["text"] = text
    for style in content_obj.get("styles", []):
        style["range"] = [0, len(text)]
    new_text["content"] = json.dumps(content_obj, ensure_ascii=False)
    apply_subtitle_outline(new_text, border_width=0.15)
    text_materials.append(new_text)

    new_tseg = copy.deepcopy(orig_tseg)
    new_tseg.update({
        "id": new_id(), "material_id": mat_id,
        "extra_material_refs": [], "source_timerange": None,
        "target_timerange": {"start": start_us, "duration": dur_us},
        "render_index": 14000 + render_idx,
    })
    new_tseg.setdefault("clip", {}).setdefault("transform", {})
    new_tseg["clip"]["transform"]["y"] = -0.7407407407407407
    text_segments.append(new_tseg)

text_track = {
    "id": new_id(), "attribute": 0, "flag": 0,
    "is_default_name": True, "name": "", "type": "text",
    "segments": text_segments,
}

# ★ 비디오 트랙은 그대로, 자막 트랙만 추가
draft["materials"]["texts"] = text_materials
draft["tracks"] = [draft["tracks"][0], text_track]

draft_str = json.dumps(draft, ensure_ascii=False, indent=2)
for p in [proj_dir/"draft_info.json", proj_dir/"draft_info.json.bak",
          tl_dir/"draft_info.json", tl_dir/"draft_info.json.bak"]:
    p.write_text(draft_str, encoding="utf-8")

print(f"✓ 비디오: {len(draft['tracks'][0]['segments'])}개 유지")
print(f"✓ 자막: {len(text_segments)}개 추가")
```

---

## 캐시 활용

| 파일 존재 | 건너뛰는 단계 |
|----------|-------------|
| `{PROJECT}_edited_words.json` | Whisper 전사 |
| `/tmp/subtitle_input.json` | dump 단계 |
| `/tmp/subtitle_splits.json` | subtitle-splitter |

## 핵심 주의사항

- **모드 B 원리**: ffconcat으로 편집 오디오를 재구성하면 Whisper 타임스탬프 = CapCut 타임라인 타임스탬프. 별도 시간 변환 불필요.
- **`add_subtitles.py` 직접 호출 금지** (모드 B): 비디오 트랙을 단일 세그먼트로 교체함. 반드시 커스텀 Python 스크립트(단계 6) 사용.
- **uv run 필수**: `add_subtitles.py`는 Python 3.10+ 문법 사용. `cd ${SCRIPTS} && uv run python3 script.py`.
- **4개 파일 동시 저장**: `draft_info.json`, `.bak`, `Timelines/UUID/draft_info.json`, `.bak`.
- **CapCut 종료 필수**: 실행 중 파일 수정해도 CapCut 재시작 시 덮어씀.
