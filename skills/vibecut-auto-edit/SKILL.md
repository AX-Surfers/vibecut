---
name: vibecut-auto-edit
version: 0.1.0
description: |
  CapCut 영상에 한국어 자막을 자동 추가하거나, 무음 구간을 자동 제거합니다.
  Whisper 전사 → 검증 → CapCut JSON 직접 수정의 전체 파이프라인.
  트리거: "캡컷 편집", "컷편집", "무음 제거", "자막 추가", "자막 올려", "/vibecut"
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

# vibecut-auto-edit 스킬

CapCut(macOS) 프로젝트 JSON을 직접 수정해 다음을 자동화합니다:

1. **한국어 자막 추가** — Whisper 전사 + 오인식 자동 교정 + 단어 단위 싱크
2. **무음 구간 제거** — -35dB 기반 컷편집
3. **CapCut JSON 적용** — 4개 파일 동시 갱신, .locked 자동 삭제

## 전제 조건

```bash
# CapCut 종료 확인
ps aux | grep -i "CapCut.app/Contents/MacOS/CapCut" | grep -v grep
# 출력 없어야 함 (있으면 스크립트가 자동 종료)

# ffmpeg 설치 확인
which ffmpeg && which ffprobe

# uv 설치 확인 (없으면)
curl -LsSf https://astral.sh/uv/install.sh | sh
```

사용자의 CapCut에 **적어도 1개의 프로젝트**가 있어야 합니다 (템플릿용).

## 스크립트 경로

플러그인 설치 시 `${CLAUDE_PLUGIN_ROOT}` 환경변수로 접근:

```bash
SCRIPTS="${CLAUDE_PLUGIN_ROOT}/scripts"
DATA="${CLAUDE_PLUGIN_ROOT}/data"
```

## 실행 흐름 — 자막 추가

### 1단계: 입력 확인 + Whisper 모델 선택 (Human-in-the-Loop)

```python
# 영상 파일 확인
# AskUserQuestion으로 Whisper 모델 선택:
#   - small (기본, 5분 영상 ~3분)
#   - tiny / base / medium / large-v3
```

### 2단계: Whisper 전사 + 단어 타임스탬프

```bash
uv run "${SCRIPTS}/add_subtitles.py" "${VIDEO}" \
  --model "${WHISPER_MODEL}" \
  --no-verify
# → <video>.srt, <video>_words.json 생성
```

### 3단계: 누적 사전 1차 적용 (자동, 무료)

`data/corrections.json` 사전이 SRT에 자동 적용됩니다. 23개 한국어 IT 오인식 패턴.

### 4단계: subtitle-verifier 에이전트 호출

```python
Task(
    description="자막 한국어 오타 검증",
    subagent_type="subtitle-verifier",
    prompt=f"{video.stem}.srt를 검증하고 {video.stem}_verified.srt로 저장해줘. "
           f"사전에 없는 새 오인식 패턴은 corrections.json에 추가해줘."
)
```

### 5단계: 검증된 자막을 CapCut에 적용

```bash
uv run "${SCRIPTS}/add_subtitles.py" "${VIDEO}" \
  --srt "${VIDEO%.*}_verified.srt"
# → CapCut 프로젝트 생성 + 자막 트랙 추가
#   - 단어 순차 매칭 (싱크 정확)
#   - 18자 분리 (잘림 방지)
#   - 종결어미 머지 (자연스러운 분리)
#   - 검은 외곽선 (가독성)
#   - 인접 자막 겹침 제거
```

## 실행 흐름 — 무음 제거

### 1단계: 무음 구간 감지

```bash
ffmpeg -i "${VIDEO}" -af silencedetect=noise=-35dB:d=0.5 -f null - 2>&1 \
  | grep -E "silence_(start|end)" > /tmp/silence_data.txt
```

### 2단계: 발화 구간 계산

```bash
uv run "${SCRIPTS}/make_segments.py" \
  --speech /tmp/speech_segments.json \
  --out /tmp/final_segments.json
```

파라미터 (사용자 실제 편집 패턴 반영):
- 무음 임계값: -35dB
- 최소 무음: 0.5초
- 갭 병합: 0.5초
- 최소 발화: 0.3초

### 3단계: CapCut JSON 적용

```bash
uv run "${SCRIPTS}/capcut_editor.py" /tmp/final_segments.json
# → 4개 파일 동시 갱신 + .locked 삭제
```

## 핵심 규칙 (모든 단계)

| 규칙 | 이유 |
|------|------|
| CapCut 완전 종료 후 실행 | 실행 중 변경사항은 무시됨 |
| 4개 JSON 파일 동시 저장 | 하나만 변경 시 CapCut이 .bak에서 복원 |
| .locked 파일 삭제 | 잠금 풀어야 변경 인식 |
| 30fps 프레임 정렬 | 타임스탬프 부동소수점 오차 방지 |
| 단어 타임스탬프 활용 | 균등 분할 시 빠른/느린 발화에서 싱크 어긋남 |
| 사용자 모델 선택 (HITL) | tiny~large-v3, 정확도 vs 시간 트레이드오프 |

## 출력 결과

```
✓ CapCut 'myvideo' 프로젝트 생성 완료
  - 자막: 134개 세그먼트 (단어 단위 싱크)
  - 검증: 12건 교정 ("챕포"→"챗봇" 외)
  - 사전: 3개 새 패턴 추가 → corrections.json
  - 가독성: 검은 외곽선 (border_width=0.15)

→ CapCut을 열어 'myvideo' 프로젝트를 확인하세요
```

## 자세한 가이드

- 에이전트: `agents/capcut/AGENT.md`, `agents/subtitle-verifier/AGENT.md`
- 범용 (Codex CLI 등): 저장소 루트의 `AGENTS.md`
- 한국어 사전 기여: `data/corrections.json` PR로 추가 가능
