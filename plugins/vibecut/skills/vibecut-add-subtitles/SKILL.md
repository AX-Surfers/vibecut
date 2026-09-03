---
name: vibecut-add-subtitles
version: 0.7.0
description: |
  영상 또는 기존 CapCut 프로젝트에 한국어 자막을 자동 생성·적용.
  Whisper 전사 → 문법 경계 분할 → CapCut JSON 적용 (다른 트랙은 보존).
  vibecut-auto-edit의 컷 승인 이후 자동 연결 단계(모드 B)로도 호출됨.
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
| 이미 편집된 CapCut 프로젝트에 자막 추가, 원본 `{stem}_words.json` 있음 | **모드 B (컷 매핑)** — 기본 |
| 이미 편집된 프로젝트인데 원본 전사가 없음 (외부에서 편집) | **모드 B 폴백 (재전사)** |

## 전제 조건 (모든 모드 공통)

```bash
VIBECUT_CONFIG="${HOME}/.vibecut/config.json"
SCRIPTS=""
if [ -f "${VIBECUT_CONFIG}" ]; then
  SCRIPTS=$(python3 -c "import json; print(json.load(open('${VIBECUT_CONFIG}')).get('scripts_dir',''))" 2>/dev/null)
fi
if [ -z "${SCRIPTS}" ]; then
  SCRIPTS=$(find "${HOME}/.claude/plugins/cache/vibecut" -name "capcut_editor.py" -maxdepth 8 2>/dev/null | head -1 | xargs dirname 2>/dev/null)
fi
if [ -z "${SCRIPTS}" ]; then
  APP_DIR="${HOME}/.vibecut/app"
  if [ -d "${APP_DIR}/.git" ]; then
    git -C "${APP_DIR}" pull --ff-only >/dev/null 2>&1
  else
    git clone --depth 1 https://github.com/AX-Surfers/Vibecut.git "${APP_DIR}" >/dev/null 2>&1
  fi
  [ -f "${APP_DIR}/scripts/capcut_editor.py" ] && SCRIPTS="${APP_DIR}/scripts"
fi
[ -z "${SCRIPTS}" ] && echo "❌ vibecut-setup 먼저 실행" && exit 1

uv run "${SCRIPTS}/_platform.py" quit-capcut
WHISPER_MODEL="large-v3-turbo"   # 질문 없이 고정. 사용자가 이번만 다른 모델을 말하면 그 값
```

⚠ 커뮤니티 한국어 fine-tune 모델은 기본으로 쓰지 않습니다 — 긴 오디오 대부분을 누락한
사례가 있습니다. 배경과 안전 절차는 `vibecut-auto-edit` 단계 1 참고.

---

## 모드 B: 기존 CapCut 프로젝트에 자막 추가 (컷 매핑, 기본)

```
{stem}_words.json + CapCut 컷 정보
  ├─ [1] 프로젝트·타임라인 확인 (find_project.py)
  ├─ [2] subtitles_from_cuts.py  → {stem}_subtitle_input.json  (자막 경계 == 컷 경계, 오인식 사전 적용)
  ├─ [3] subtitle-splitter 에이전트 → {stem}_subtitle_splits.json
  └─ [4] apply_subtitles.py       → 자막 트랙 추가, 다른 트랙 보존, 4개 JSON 갱신 + 백업
```

왜 재전사하지 않는가: 편집 오디오를 이어붙여 다시 전사하면 Whisper가 하드컷을 무시해
자막이 컷을 가로지르고 같은 문장이 두 번 나왔습니다. 컷은 원본 words.json의 경계로
만들어졌으므로 같은 단어 시각을 컷 매핑으로 옮기면 경계가 구조적으로 일치합니다.

### 단계 1: 프로젝트·타임라인

```bash
VIDEO="<원본 영상 경로>"; STEM="${VIDEO%.*}"
uv run "${SCRIPTS}/find_project.py" "${VIDEO}"     # <프로젝트>\t<타임라인 이름>\t<id>
PROJECT="<CapCut 프로젝트 경로>"
TIMELINE="<타임라인 이름 또는 빈 문자열>"
TL_OPT=(); [ -n "${TIMELINE}" ] && TL_OPT=(--timeline "${TIMELINE}")
```

타임라인이 여러 개면 `--timeline` 없이는 스크립트가 목록을 보여주고 멈춥니다.

### 단계 2: 컷 매핑으로 자막 입력 생성

```bash
uv run "${SCRIPTS}/subtitles_from_cuts.py" --words "${STEM}_words.json" \
  --project "${PROJECT}" "${TL_OPT[@]}"
# → {STEM}_subtitle_input.json
```

`✓ 모든 자막이 컷 경계 안에 있고…`를 확인합니다. `자막 없는 컷: [N]`은 그 컷에 단어가
없는 것(호흡 구간)이라 정상입니다. 단어는 가장 많이 겹치는 컷에 붙으므로 컷 경계에서
글자가 빠지지 않습니다("한 달"→"달" 실패 사례 수정됨).

### 단계 3: subtitle-splitter 에이전트

```
{STEM}_subtitle_input.json 을 읽어 한국어 문법 경계로 분할하고
{STEM}_subtitle_splits.json 에 저장해줘. 세그먼트 수 1:1 대응, 각 원소는 텍스트 조각 배열.
```

`subtitle_splits.json`은 **인덱스가 아니라 텍스트 조각 배열**입니다:
```json
[["이번 영상에서는 프롬프트를 작성할 때", "엔트로픽 개발자가 사용하는 방법과"], ["네 감사합니다"]]
```
`apply_subtitles.py`가 개수와 형식을 검사하고, 공백 제거 후 원문과 다른 조각은 경고합니다.

### 단계 4: 적용 (다른 트랙 보존)

```bash
uv run "${SCRIPTS}/_platform.py" quit-capcut
uv run "${SCRIPTS}/apply_subtitles.py" --project "${PROJECT}" "${TL_OPT[@]}" \
  --input "${STEM}_subtitle_input.json" --splits "${STEM}_subtitle_splits.json"
```

- 비디오·오디오·이미지·사용자가 직접 넣은 텍스트 트랙은 모두 그대로 둡니다.
  (예전 인라인 코드는 `tracks = [tracks[0], 자막]`으로 전부 지웠습니다 — 폐기)
- vibecut이 만든 자막 트랙(이름 `vibecut`)만 교체합니다. 예전 버전이 만든 이름 없는 자막
  트랙은 로그에 `보존 (교체하려면 --replace-track N)`으로 뜨니, 겹치지 않게 그 옵션으로 재실행합니다.
- 4개 파일(루트 + `Timelines/<uuid>/`) 동시 저장, `.vibecut_backups/`에 자동 백업, `.locked` 삭제.
  메인이 아닌 타임라인은 루트 파일을 건드리지 않습니다.

---

## 모드 A: 원본 영상 기준 (새 프로젝트 생성)

```
영상
  ├─ [1] add_subtitles.py --no-verify --dump-only → /tmp/subtitle_input.json
  ├─ [2] subtitle-splitter 에이전트 → /tmp/subtitle_splits.json
  └─ [3] add_subtitles.py --splits → 새 CapCut 프로젝트 생성
```

```bash
VIDEO="<영상 파일 경로>"
uv run "${SCRIPTS}/add_subtitles.py" "${VIDEO}" --model "${WHISPER_MODEL}" --no-verify --dump-only
```

subtitle-splitter 에이전트 호출:
```
/tmp/subtitle_input.json을 읽어 한국어 문법 경계로 분할하고 /tmp/subtitle_splits.json에 저장해줘.
세그먼트 수 1:1 대응 필수, 각 원소는 텍스트 조각 배열.
```

```bash
uv run "${SCRIPTS}/add_subtitles.py" "${VIDEO}" --srt "${VIDEO%.*}.srt" --no-verify \
  --splits /tmp/subtitle_splits.json
```

---

## 모드 B 폴백: 원본 전사가 없는 프로젝트 (재전사)

원본 `{stem}_words.json`이 없을 때만 씁니다. 편집본 오디오를 만들어 전사하고, 그 결과를
subtitle_input으로 만든 뒤 위 모드 B의 단계 3~4를 그대로 씁니다.

### 1. 편집 오디오 추출 — `-ss/-t` 개별 추출 (ffconcat 금지)

`ffmpeg -f concat` + `inpoint/outpoint`는 구간당 약 0.4초씩 초과 추출되어 38컷에서
**16.8초**가 밀렸습니다 (실전 실패 사례). 각 구간을 `-ss/-t`로 따로 뽑아 이어붙입니다.

```bash
PROJECT_NAME="$(basename "${PROJECT}")"
uv run python - "${PROJECT}" "${TIMELINE}" "/tmp/${PROJECT_NAME}_edited.raw" "${SCRIPTS}" <<'PYEOF'
import json, subprocess, sys
from pathlib import Path
project, timeline, raw, scripts = sys.argv[1:5]
sys.path.insert(0, scripts)
from capcut_editor import draft_path_for, pick_video_track, resolve_timeline
proj = Path(project)
tl, _ = resolve_timeline(proj, timeline or None)
data = json.loads(draft_path_for(proj, tl).read_text(encoding="utf-8"))
mats = {v["id"]: v["path"] for v in data["materials"]["videos"]}
track = data["tracks"][pick_video_track(data)]
segs = sorted(track["segments"], key=lambda s: s["target_timerange"]["start"])
with open(raw, "wb") as f:
    for seg in segs:
        src = seg["source_timerange"]
        p = subprocess.run(["ffmpeg", "-v", "error",
            "-ss", f"{src['start']/1e6:.6f}", "-t", f"{src['duration']/1e6:.6f}",
            "-i", mats[seg["material_id"]], "-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", "-"],
            capture_output=True)
        f.write(p.stdout)
print(f"타임라인 길이: {data['duration']/1e6:.2f}s")
PYEOF
ffmpeg -y -v error -f s16le -ar 16000 -ac 1 -i "/tmp/${PROJECT_NAME}_edited.raw" \
  -acodec pcm_s16le "/tmp/${PROJECT_NAME}_edited.wav"
ffprobe -v error -show_entries format=duration -of csv=p=0 "/tmp/${PROJECT_NAME}_edited.wav"
```

**검증 필수**: wav 길이와 위에서 출력한 타임라인 길이가 **0.1초 이내**로 일치해야 합니다.
어긋나면 그대로 자막 오차가 됩니다.

### 2. 전사 → subtitle_input

```bash
uv run "${SCRIPTS}/transcribe.py" "/tmp/${PROJECT_NAME}_edited.wav" --model "${WHISPER_MODEL}"
# → /tmp/{PROJECT_NAME}_edited_words.json (타임라인 기준 시각)
```

words.json의 세그먼트를 그대로 `{text, words, start, end}` 배열로 저장하면 subtitle_input
형식과 같습니다 (`/tmp/${PROJECT_NAME}_subtitle_input.json`). 이후 모드 B의 단계 3(분할) →
단계 4(`apply_subtitles.py --input … --splits …`).

---

## 핵심 주의사항

- **`add_subtitles.py`를 기존 프로젝트에 직접 호출 금지** — 비디오 트랙을 단일 세그먼트로
  교체합니다. 기존 프로젝트에는 반드시 `apply_subtitles.py`.
- **uv run 필수**: 스크립트는 Python 3.11+ 문법을 씁니다.
- **CapCut 종료 필수**: 실행 중 파일을 수정해도 CapCut 재시작 시 덮어씁니다.
- **오인식 교정**: `data/corrections.json` 사전은 `subtitles_from_cuts.py`와 `add_subtitles.py`
  양쪽에서 자동 적용됩니다. 새 오인식을 발견하면 사전에 추가합니다.
