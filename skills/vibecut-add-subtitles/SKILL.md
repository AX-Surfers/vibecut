---
name: vibecut-add-subtitles
version: 0.1.0
description: |
  영상에 한국어 자막을 자동으로 추가합니다. Whisper 전사 → 누적 사전으로 1차 교정 →
  subtitle-verifier 에이전트로 검증 → 단어 단위 싱크 + 18자 분리 + 검은 외곽선으로
  CapCut 프로젝트에 적용합니다.
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

```
영상 (.mov/.mp4)
   │
   ├─ [1] Whisper 전사 (단어 타임스탬프 포함)
   │        ↓ <video>.srt + <video>_words.json
   │
   ├─ [2] 누적 사전 1차 적용 (corrections.json, 자동·무료)
   │        ↓ 알려진 패턴 자동 교정
   │
   ├─ [3] subtitle-verifier 에이전트 호출
   │        ↓ <video>_verified.srt + 사전 자동 업데이트
   │
   ├─ [4] 단어 순차 매칭 + 18자 분리 + 종결어미 머지
   │        ↓ 음성-자막 정확한 싱크
   │
   └─ [5] CapCut JSON 4개 파일 동시 갱신 + 검은 외곽선
              ↓ 편집 가능한 상태로 결과 제공
```

## 전제 조건

```bash
# CapCut 종료 확인 (자동 종료됨)
ps aux | grep "CapCut.app/Contents/MacOS/CapCut" | grep -v grep

# ffmpeg
which ffprobe

# uv (Python 의존성 자동 관리)
which uv || curl -LsSf https://astral.sh/uv/install.sh | sh
```

사용자의 CapCut에 **적어도 1개의 프로젝트**가 있어야 합니다 (템플릿용).

## 실행 흐름

### 단계 1: 입력 확인

```bash
# 영상 파일 인자 확인. 없으면 사용자에게 물어보거나 현재 디렉토리 탐색
find . -maxdepth 2 -type f \( -name "*.mov" -o -name "*.mp4" \) | head -5
```

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
SCRIPTS="${CLAUDE_PLUGIN_ROOT}/scripts"
uv run "${SCRIPTS}/add_subtitles.py" "${VIDEO}" \
  --model "${WHISPER_MODEL}" \
  --no-verify
# → <video>.srt, <video>_words.json 생성
```

**왜 단어 타임스탬프가 필요한가:** 시간 균등 분할 시 빠른 발화 / 느린 발화에서 싱크가 어긋남. 단어별 시작·끝 시간을 알아야 정확한 분리가 가능.

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

### 단계 5: 검증된 자막을 CapCut에 적용

```bash
uv run "${SCRIPTS}/add_subtitles.py" "${VIDEO}" \
  --srt "${VIDEO%.*}_verified.srt"
```

스크립트가 자동 처리하는 작업:
1. **단어 순차 매칭** — 각 단어는 정확히 한 자막에만 할당 (중복 매칭 방지)
2. **18자 단위 분리** — 한국어 자막 화면 잘림 방지
3. **종결어미 머지** — "됩니다.", "되겠죠" 등 짧은 꼬리를 앞 자막에 합침
4. **겹침 제거** — 인접 자막 시간 겹침 자동 후처리 (`min_gap=0.02s`)
5. **검은 외곽선** — `border_width=0.15` + `content.styles[].strokes` (가독성)
6. **30fps 프레임 정렬** — CapCut 타임스탬프 누적 오차 방지
7. **4개 JSON 파일 동시 저장** — `draft_info.json` 루트/bak + `Timelines/UUID/` 루트/bak
8. **`.locked` 파일 자동 삭제**
9. **`root_meta_info.json` 등록** — CapCut UI에 프로젝트가 나타남

### 단계 6: 결과 보고

```
✓ CapCut '<프로젝트명>' 프로젝트 생성 완료
  - 자막: NNN개 세그먼트 (단어 단위 싱크)
  - 검증: X건 교정 (예: "챕포"→"챗봇", "재미나"→"Gemini")
  - 사전 학습: Y개 새 패턴 추가 → corrections.json
  - 가독성: 검은 외곽선 적용

→ CapCut을 열어 '<프로젝트명>' 프로젝트를 확인하세요
```

## 사용자 호출 예시

| 사용자 발화 | 스킬 동작 |
|------------|----------|
| "자막 추가해줘" | 영상 파일 선택 → 모델 선택 → 전체 파이프라인 |
| "before.mov에 자막 올려" | 1단계 생략, 바로 2~6단계 |
| "이 영상 자막 만들어" | 현재 디렉토리에서 영상 탐색 |
| "검증 없이 자막만 빨리" | `--no-verify` 플래그 + 4단계 생략 |

## 캐시 활용 (재실행 시)

| 파일 존재 | 동작 |
|----------|------|
| `<video>_verified.srt` | Whisper + 검증 모두 생략, 바로 적용 |
| `<video>.srt` + `<video>_words.json` | Whisper 생략, 검증부터 |
| `<video>.srt`만 | Whisper 생략, 검증부터 (균등 분할로 폴백) |
| 없음 | 전체 파이프라인 |

## 환경 변수 (선택)

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `VIBECUT_TEMPLATE_NAME` | (자동 감지) | CapCut 템플릿 프로젝트명 |
| `VIBECUT_CORRECTIONS` | `data/corrections.json` | 사전 파일 경로 |

## 주의사항

- **CapCut 실행 중 실행 시**: 스크립트가 자동으로 종료하지만, 사용자가 작업 중이면 데이터 손실 가능
- **`--no-verify` 사용 시**: 한국어 오인식이 그대로 남음 → 다른 사용자에게 권장하지 않음
- **첫 사용자의 경우**: `corrections.json`은 비어있거나 작음 → 첫 영상은 검증 시간이 더 걸림
