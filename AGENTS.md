# AGENTS.md — Vibecut

> 이 파일은 **Codex CLI** 및 기타 AGENTS.md 호환 도구에서 자동으로 읽힙니다.  
> Claude Code 사용자는 `.claude-plugin/plugin.json`을 통해 더 풍부한 에이전트/스킬을 사용할 수 있습니다.

## 프로젝트 개요

**Vibecut**은 CapCut(macOS) 영상 프로젝트의 JSON을 직접 수정하여 컷편집과 자막을 자동화하는 도구입니다.

### 제공 기능

| 기능 | 스크립트 | 설명 |
|------|---------|------|
| Whisper 자막 생성 | `scripts/add_subtitles.py` | faster-whisper로 한국어 자막 + 단어 타임스탬프 추출 |
| 한국어 오인식 자동 교정 | `data/corrections.json` | 누적 사전으로 매번 같은 오인식 자동 처리 |
| 컷편집 (무음 제거) | `scripts/make_segments.py` | -35dB 이하 무음 구간 자동 감지 |
| CapCut JSON 적용 | `scripts/capcut_editor.py` | 4개 파일 동시 갱신, .locked 자동 삭제, 30fps 정렬 |

## Codex CLI 사용법

```bash
# 1. 자막 생성 + CapCut 프로젝트 자동 생성
codex --exec "Vibecut을 사용해 ~/Movies/test.mov에 자막을 자동으로 추가해줘. \
  scripts/add_subtitles.py를 호출하고, Whisper 모델은 small을 사용해."

# 2. 검증된 자막으로 적용
codex --exec "before.srt를 검증해서 한국어 오인식을 교정한 뒤 \
  CapCut 0531 프로젝트에 자막 트랙을 추가해줘. \
  corrections.json 사전을 활용해서 같은 패턴은 자동 처리."

# 3. 무음 제거 컷편집
codex --exec "make_segments.py로 무음 구간을 계산한 뒤 \
  capcut_editor.py로 CapCut JSON에 적용해줘."
```

## 직접 스크립트 호출 (uv 사용)

스크립트들은 [uv](https://docs.astral.sh/uv/) inline 메타데이터를 포함합니다. 별도 가상환경 불필요:

```bash
# 의존성 자동 설치 + 실행 (uv가 없으면: curl -LsSf https://astral.sh/uv/install.sh | sh)
uv run scripts/add_subtitles.py <video.mov> --model small
uv run scripts/make_segments.py --speech speech.json --out segments.json
uv run scripts/capcut_editor.py segments.json
```

## 핵심 규칙 (모든 에이전트가 준수)

### 1. CapCut 4개 파일 동시 저장 (필수)

```
<project>/draft_info.json
<project>/draft_info.json.bak
<project>/Timelines/<UUID>/draft_info.json
<project>/Timelines/<UUID>/draft_info.json.bak
```

하나라도 빠지면 CapCut이 변경사항을 무시합니다.

### 2. .locked 파일 삭제 (필수)

편집 후 `.locked` 파일이 있으면 반드시 삭제. CapCut이 백업에서 복원합니다.

### 3. 30fps 프레임 정렬

```python
FPS = 30
def frame_to_us(frame: int) -> int:
    return frame * 1_000_000 // FPS  # 반올림 처리 별도
```

타임스탬프 누적은 µs가 아닌 프레임 번호로 (부동소수점 오차 방지).

### 4. 단어 타임스탬프 기반 자막 분리

긴 자막을 시간 균등 분할하면 빠른/느린 발화에서 싱크가 어긋남.  
`word_timestamps=True`로 추출한 단어 시작/끝 시간을 활용해 자르고, 자막 간 시간 겹침은 `remove_overlaps()`로 제거.

### 5. 한국어 자막 줄당 18자 제한

CapCut에서 19자 이상이면 화면 오른쪽 잘림. `split_with_word_sync()` + 종결어미 머지로 자연스럽게 분리.

### 6. 검은 외곽선 필수 (가독성)

흰 자막은 흰 배경에서 안 보임. `border_width: 0.15` + `content.styles[].strokes` 둘 다 설정.

## 디렉토리 구조

```
Vibecut/
├── .claude-plugin/         # Claude Code 플러그인 매니페스트
│   ├── plugin.json
│   └── marketplace.json
├── agents/                 # Claude Code 에이전트
│   ├── capcut/AGENT.md
│   └── subtitle-verifier/AGENT.md
├── skills/                 # Claude Code 스킬
│   └── vibecut-auto-edit/SKILL.md
├── scripts/                # 공통 Python 스크립트 (uv-ready)
│   ├── add_subtitles.py
│   ├── capcut_editor.py
│   └── make_segments.py
├── data/
│   └── corrections.json    # 한국어 오인식 사전 (누적)
├── AGENTS.md               # 이 파일 (Codex CLI / 범용)
├── README.md
├── pyproject.toml          # uv 의존성
└── LICENSE
```

## 자세한 가이드

- **Claude Code 사용자**: `agents/capcut/AGENT.md` 및 `skills/vibecut-auto-edit/SKILL.md` 참조
- **Codex CLI 사용자**: 이 AGENTS.md + `README.md` 참조
- **스크립트 직접 사용**: `scripts/*.py` 각 파일 상단의 docstring 참조

## 라이선스

MIT
