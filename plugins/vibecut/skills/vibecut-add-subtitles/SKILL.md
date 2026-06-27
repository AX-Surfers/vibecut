---
name: vibecut-add-subtitles
version: 0.4.0
description: |
  영상에 한국어 자막을 자동으로 추가합니다. Whisper 전사 → 누적 사전으로 1차 교정 →
  subtitle-verifier로 검증 → subtitle-splitter 서브에이전트로 자연스러운 문장 경계 분할 →
  단어 단위 싱크 + 검은 외곽선으로 CapCut 프로젝트에 적용합니다.
  auto-edit(무음 제거) 이후 실행 시 편집된 타임라인 기준으로 오디오를 추출해 자막을 생성합니다.
  트리거: "자막 추가", "자막 올려", "자막 만들어", "subtitle add", "/vibecut-add-subtitles"
metadata:
  category: video
  locale: ko-KR
allowed-tools:
  - Bash
  - Read
  - Write
  - Edit
  - Task
  - AskUserQuestion
---

# vibecut-add-subtitles 스킬

**영상 → 한국어 자막 자동 생성 → CapCut 프로젝트 적용** 의 완전 자동 파이프라인.

## 핵심 처리 흐름

### 모드 A: 원본 영상 기준 (기본)

```
영상 (.mov/.mp4)
   │
   ├─ [1] Whisper 전사 (단어 타임스탬프 포함)
   │        ↓ <video>.srt + <video>_words.json + /tmp/subtitle_input.json
   │
   ├─ [2] 누적 사전 1차 적용 (corrections.json, 자동·무료)
   │        ↓ 알려진 패턴 자동 교정
   │
   ├─ [3] subtitle-verifier 에이전트 호출
   │        ↓ <video>_verified.srt + 사전 자동 업데이트
   │
   ├─ [4] 검증본 기준 subtitle_input.json 재덤프 (--dump-only)
   │        ↓ 검증된 텍스트 + 단어 타임스탬프
   │
   ├─ [5] subtitle-splitter 서브에이전트 호출
   │        ↓ /tmp/subtitle_splits.json (한국어 문법 경계 기준 분할)
   │
   ├─ [6] 단어 순차 매칭 + AI 분할 적용 + 종결어미 머지
   │        ↓ 음성-자막 정확한 싱크
   │
   └─ [7] CapCut JSON 4개 파일 동시 갱신 + 검은 외곽선
              ↓ 편집 가능한 상태로 결과 제공
```

### 모드 B: 편집 타임라인 기준 (auto-edit 이후)

`final_segments.json` 또는 `speech_segments.json`이 있거나 `--segments`로 명시할 때 자동 활성화.

```
영상 + speech_segments.json (auto-edit 결과)
   │
   ├─ [0] ffmpeg로 각 발화 구간만 오디오 추출 → 연결
   │        ↓ <video>_edited_audio.wav
   │        (연결된 오디오 타임스탬프 = CapCut 편집 타임라인 타임스탬프)
   │
   ├─ [1] Whisper 전사 (_edited_audio.wav 기준)
   │        ↓ <video>_edited.srt + <video>_edited_words.json
   │
   ├─ [2~6] 사전 교정 → 검증 → 재덤프 → 분할 → 싱크 (동일)
   │
   └─ [7] CapCut JSON 적용
              ↓ 자막이 편집된 타임라인과 정확히 맞아떨어짐
```

**핵심 원리:** 발화 구간을 붙여서 만든 오디오의 시간 = CapCut이 세그먼트를 이어붙인 타임라인의 시간. 별도의 시간 변환 없이 Whisper 타임스탬프가 그대로 CapCut 자막 위치가 됨.

## 전제 조건

```bash
# CapCut 실행 여부 확인 후 강제 종료
# 실행 중에 draft_info.json을 수정해도 CapCut이 덮어쓰므로 반드시 먼저 종료
if pgrep -i "CapCut" > /dev/null 2>&1; then
  echo "⚠ CapCut 실행 중 — 강제 종료합니다..."
  pkill -i "CapCut"
  sleep 2
  echo "✅ CapCut 종료 완료"
else
  echo "✅ CapCut 종료 상태 확인"
fi

# ffmpeg
which ffprobe

# uv (Python 의존성 자동 관리)
which uv || curl -LsSf https://astral.sh/uv/install.sh | sh
```

사용자의 CapCut에 **적어도 1개의 프로젝트**가 있어야 합니다 (템플릿용).

## 실행 흐름

### 단계 1: 입력 확인 + 편집 타임라인 감지

```bash
# 영상 파일 인자 확인. 없으면 사용자에게 물어보거나 현재 디렉토리 탐색
find . -maxdepth 2 -type f \( -name "*.mov" -o -name "*.mp4" \) | head -5

# auto-edit 결과 segments 파일 자동 탐색
SEGMENTS_FILE=""
for f in final_segments.json speech_segments.json /tmp/final_segments.json /tmp/speech_segments.json; do
  [ -f "$f" ] && SEGMENTS_FILE="$f" && break
done
# SEGMENTS_FILE이 있으면 --segments 플래그로 전달 (모드 B 자동 활성화)
```

**판단 기준:** `final_segments.json` 우선, 없으면 `speech_segments.json`. 둘 다 없으면 원본 영상 기준(모드 A)으로 진행.

### 단계 2: Whisper 모델 선택 (Human-in-the-Loop)

**기존 SRT가 없을 때만** 사용자에게 모델 선택을 요청. `<video>.srt` 또는 `<video>_verified.srt`가 이미 있으면 캐시 재사용.

```python
AskUserQuestion(questions=[{
    "question": "Whisper 모델을 선택해주세요 (영상 길이에 따라 시간이 다름)",
    "header": "Whisper 모델",
    "multiSelect": False,
    "options": [
        {"label": "small (Recommended)",
         "description": "5분 영상 ~3분. 균형 잡힌 정확도. 대부분의 경우 권장."},
        {"label": "tiny",
         "description": "~30초. 가장 빠르지만 한국어 정확도가 낮음. 빠른 미리보기용."},
        {"label": "base",
         "description": "~1분. 보통 수준. small이 안 되는 환경의 대안."},
        {"label": "medium",
         "description": "~10-20분. 매우 정확하지만 시간이 오래 걸림. 중요한 영상에 권장."},
        {"label": "large-v3",
         "description": "~30-60분. 최고 정확도. 인터뷰/강의처럼 정확도가 최우선인 경우."},
    ]
}])
```

### 단계 3: Whisper 전사 (단어 타임스탬프 필수)

```bash
# scripts 경로 결정: config 우선, 없으면 플러그인 캐시 자동 탐색
VIBECUT_CONFIG="${HOME}/.vibecut/config.json"
SCRIPTS=""
if [ -f "${VIBECUT_CONFIG}" ]; then
  SCRIPTS=$(python3 -c "import json; print(json.load(open('${VIBECUT_CONFIG}')).get('scripts_dir',''))" 2>/dev/null)
fi
if [ -z "${SCRIPTS}" ]; then
  SCRIPTS=$(find "${HOME}/.claude/plugins/cache/vibecut" -name "capcut_editor.py" -maxdepth 8 2>/dev/null | head -1 | xargs dirname 2>/dev/null)
fi
if [ -z "${SCRIPTS}" ]; then
  echo "❌ vibecut 스크립트를 찾을 수 없습니다. '/vibecut-setup'을 먼저 실행해주세요."
  exit 1
fi

# 모드 A: 원본 영상 기준
uv run "${SCRIPTS}/add_subtitles.py" "${VIDEO}" \
  --model "${WHISPER_MODEL}" \
  --no-verify
# → <video>.srt, <video>_words.json 생성

# 모드 B: 편집 타임라인 기준 (SEGMENTS_FILE이 있을 때)
uv run "${SCRIPTS}/add_subtitles.py" "${VIDEO}" \
  --model "${WHISPER_MODEL}" \
  --segments "${SEGMENTS_FILE}" \
  --no-verify
# → <video>_edited_audio.wav 추출 후 전사
# → <video>_edited.srt, <video>_edited_words.json 생성
```

**왜 단어 타임스탬프가 필요한가:** 시간 균등 분할 시 빠른 발화 / 느린 발화에서 싱크가 어긋남. 단어별 시작·끝 시간을 알아야 정확한 분리가 가능.

> `add_subtitles.py`는 전사 후 `/tmp/subtitle_input.json`을 자동 저장합니다.

### 단계 4: subtitle-verifier 에이전트 호출

```python
Task(
    description="자막 한국어 오타 검증",
    subagent_type="subtitle-verifier",
    prompt=(
        f"{video.stem}.srt 파일을 검증하고 {video.stem}_verified.srt로 저장해줘. "
        f"corrections.json 사전을 활용하고, 새로 발견된 오인식 패턴은 사전에 추가해줘. "
        f"영상 컨텍스트: [영상 주제 추측]"
    )
)
```

**subtitle-verifier가 하는 일:**
- `corrections.json` 사전 로드 → 이미 알려진 패턴은 자동 처리
- 사전에 없는 새 한국어 오인식 패턴 발견 → 교정
- 새 패턴을 사전에 자동 추가 (다음 영상에서 즉시 적용)
- 타임스탬프 / 자막 개수는 절대 변경 안 함

### 단계 5: 검증본 기준 subtitle_input.json 재덤프

**⚠ 순서 중요:** splitter는 반드시 **검증 후** 호출해야 합니다. 검증 전 텍스트로 분할하면
verifier가 단어를 바꿨을 때 (예: "챕포"→"챗봇") 분할 결과와 검증 텍스트가 불일치해
타임스탬프 매핑이 어긋납니다.

```bash
# 검증된 텍스트 + 단어 타임스탬프 기준으로 /tmp/subtitle_input.json 재생성
uv run "${SCRIPTS}/add_subtitles.py" "${VIDEO}" \
  --srt "${VIDEO%.*}_verified.srt" \
  --dump-only
```

### 단계 6: subtitle-splitter 서브에이전트 호출

```python
Task(
    description="한국어 자막 문법 경계 분할",
    subagent_type="subtitle-splitter",
    prompt=(
        "/tmp/subtitle_input.json을 읽어 각 세그먼트를 자연스러운 한국어 문법 경계로 분할하고 "
        "/tmp/subtitle_splits.json에 저장해줘."
    )
)
```

**subtitle-splitter가 하는 일:**
- 글자 수 제한(18자)이 아닌 **문법·호흡 경계**에서 자막 분할
- 연결어미(`~하면`, `~하고`, `~해서`), 접속사(`그리고`, `그런데`) 뒤에서 자연스럽게 끊음
- 명사구 중간, 조사와 앞 명사는 분리하지 않음
- 세그먼트 수 1:1 대응 필수 (타임스탬프 매핑 기준)

### 단계 7: 검증된 자막을 CapCut에 적용

```bash
# subtitle-splits.json이 있으면 AI 분할 결과 적용
uv run "${SCRIPTS}/add_subtitles.py" "${VIDEO}" \
  --srt "${VIDEO%.*}_verified.srt" \
  --splits /tmp/subtitle_splits.json

# splits 파일이 없는 경우 (subtitle-splitter 생략 시)
uv run "${SCRIPTS}/add_subtitles.py" "${VIDEO}" \
  --srt "${VIDEO%.*}_verified.srt"
```

스크립트가 자동 처리하는 작업:
1. **AI 분할 적용** — `/tmp/subtitle_splits.json`의 분할 결과를 단어 타임스탬프에 매핑 (`--splits` 지정 시)
2. **단어 순차 매칭** — 각 단어는 정확히 한 자막에만 할당 (중복 매칭 방지)
3. **종결어미 머지** — "됩니다.", "되겠죠" 등 짧은 꼬리를 앞 자막에 합침
4. **겹침 제거** — 인접 자막 시간 겹침 자동 후처리 (`min_gap=0.02s`)
5. **검은 외곽선** — `border_width=0.15` + `content.styles[].strokes` (가독성)
6. **30fps 프레임 정렬** — CapCut 타임스탬프 누적 오차 방지
7. **4개 JSON 파일 동시 저장** — `draft_info.json` 루트/bak + `Timelines/UUID/` 루트/bak
8. **`.locked` 파일 자동 삭제**
9. **`root_meta_info.json` 등록** — CapCut UI에 프로젝트가 나타남

### 단계 8: 결과 보고

```
✓ CapCut '<프로젝트명>' 프로젝트 생성 완료
  - 자막: NNN개 세그먼트 (단어 단위 싱크)
  - 분할: AI 문법 경계 기준 (subtitle-splitter)
  - 검증: X건 교정 (예: "챕포"→"챗봇", "재미나"→"Gemini")
  - 사전 학습: Y개 새 패턴 추가 → corrections.json
  - 가독성: 검은 외곽선 적용

→ CapCut을 열어 '<프로젝트명>' 프로젝트를 확인하세요
```

## 사용자 호출 예시

| 사용자 발화 | 스킬 동작 |
|------------|----------|
| "자막 추가해줘" | segments 파일 탐색 → 있으면 모드 B, 없으면 모드 A |
| "편집된 타임라인으로 자막 만들어" | `final_segments.json` 자동 탐색 → 모드 B |
| "before.mov에 자막 올려" | 1단계 생략, 바로 2~6단계 |
| "검증 없이 자막만 빨리" | `--no-verify` 플래그 + 4단계 생략 |

## 캐시 활용 (재실행 시)

### 모드 A (원본 기준)
| 파일 존재 | 동작 |
|----------|------|
| `<video>_verified.srt` | Whisper + 검증 모두 생략, 바로 적용 |
| `<video>.srt` + `<video>_words.json` | Whisper 생략, 검증부터 |
| `<video>.srt`만 | Whisper 생략, 검증부터 (균등 분할로 폴백) |
| 없음 | 전체 파이프라인 |

### 모드 B (편집 타임라인 기준)
| 파일 존재 | 동작 |
|----------|------|
| `<video>_edited_verified.srt` | 오디오 추출 + Whisper + 검증 모두 생략 |
| `<video>_edited.srt` + `<video>_edited_words.json` | 오디오 추출 + Whisper 생략, 검증부터 |
| 없음 | 오디오 추출 → Whisper → 검증 전체 |

## 환경 변수 (선택)

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `VIBECUT_TEMPLATE_NAME` | (자동 감지) | CapCut 템플릿 프로젝트명 |
| `VIBECUT_CORRECTIONS` | `data/corrections.json` | 사전 파일 경로 |

## 주의사항

- **CapCut 실행 중 실행 시**: 스크립트가 자동으로 종료하지만, 사용자가 작업 중이면 데이터 손실 가능
- **`--no-verify` 사용 시**: 한국어 오인식이 그대로 남음 → 다른 사용자에게 권장하지 않음
- **첫 사용자의 경우**: `corrections.json`은 비어있거나 작음 → 첫 영상은 검증 시간이 더 걸림
