# Vibecut 🎬

> CapCut 자동 편집 + Whisper 한국어 자막 + 오인식 자동 교정.  
> **Claude Code 플러그인**과 **Codex CLI** 양쪽에서 동일하게 사용 가능.

## 무엇을 할 수 있나요?

| 기능 | 스킬 / 명령 한 줄 | 결과 |
|------|-----------------------|------|
| **자막 자동 생성** | `/vibecut-add-subtitles` 또는 "자막 올려줘" | Whisper 전사 → 한국어 검수 → CapCut 자막 트랙 추가 |
| **무음 컷편집** | `/vibecut-auto-edit` 또는 "무음 제거해줘" | -35dB 이하 자동 감지 → CapCut JSON 적용 |
| **자막 검증만** | `@subtitle-verifier <name>.srt 검증해줘` | 한국어 오인식 교정 + `corrections.json` 학습 |
| **CapCut JSON 직접 수정** | `@capcut ...` 자연어 | 4개 파일 동시 갱신, .locked 자동 삭제, 30fps 정렬 |

### 핵심 특징

- 🎯 **단어 단위 정확한 싱크**: Whisper 단어 타임스탬프 활용, 시간 균등 분할 대신 실제 발화 시점에 맞춤
- 📚 **누적 학습 사전**: `corrections.json`이 사용할수록 똑똑해짐 (다음 영상에서 같은 오인식 자동 해결)
- ✂️ **18자 단위 분리**: 한국어 자막이 화면 잘림 없이 표시
- 🔤 **종결어미 머지**: "됩니다." 같은 짧은 종결어미를 앞 자막에 자동 합침
- 🖤 **검은 외곽선**: 흰 배경/터미널/코드 화면에서도 자막 가독성 보장
- ⚙️ **CapCut JSON 직접 수정**: ffmpeg 인코딩 없이 편집 가능한 상태로 결과 제공
- 🤖 **2가지 AI CLI 지원**: Claude Code 플러그인 + Codex CLI (AGENTS.md)

---

## 설치 — Claude Code

```bash
# 1. 마켓플레이스 추가
/plugin marketplace add AX-Surfers/Vibecut

# 2. Vibecut 설치
/plugin install vibecut@vibecut

# 3. (선택) Python 의존성 — uv가 자동 처리하지만, uv가 없으면 설치
curl -LsSf https://astral.sh/uv/install.sh | sh
```

설치 후 자연어로 호출:
```
@capcut before.mov에 자막을 추가해줘
```

## 설치 — Codex CLI / 일반 사용

```bash
# 1. 저장소 클론
git clone https://github.com/AX-Surfers/Vibecut.git
cd Vibecut

# 2. uv 설치 (없으면)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 3. 사용
codex --exec "AGENTS.md를 참고해서 ~/Movies/test.mov에 자막을 추가해줘"

# 또는 직접 스크립트 호출
uv run scripts/add_subtitles.py ~/Movies/test.mov --model small
```

---

## 시스템 요구사항

| 항목 | 버전 / 설명 |
|------|-----------|
| OS | macOS (CapCut 데스크탑) |
| Python | 3.11+ (uv가 자동 관리) |
| CapCut | 데스크탑 버전 (앱스토어) |
| Whisper 모델 | `tiny` ~ `large-v3` 선택 가능 (기본 `small`) |
| 디스크 | 모델 다운로드: small=~500MB, medium=~1.5GB, large-v3=~3GB |

---

## 워크플로우 다이어그램

```
영상 (.mov/.mp4)
    │
    ├─ [1] Whisper 전사 (단어 타임스탬프 포함)
    │        ↓ video.srt + video_words.json
    │
    ├─ [2] 한국어 오인식 사전 적용 (corrections.json)
    │        ↓ 1차 교정 완료
    │
    ├─ [3] subtitle-verifier 에이전트 검증
    │        ↓ video_verified.srt + 사전 자동 업데이트
    │
    ├─ [4] 단어 순차 매칭 + 18자 분리 + 종결어미 머지
    │        ↓ 최종 자막 세그먼트
    │
    └─ [5] CapCut JSON 4개 파일 동시 갱신
              ↓ <project>/draft_info.json{,.bak}
                <project>/Timelines/<UUID>/draft_info.json{,.bak}
                .locked 자동 삭제
                root_meta_info.json 등록
```

---

## 디렉토리 구조

```
Vibecut/
├── .claude-plugin/
│   ├── plugin.json              ← Claude Code 플러그인 매니페스트
│   └── marketplace.json         ← Claude Code 마켓플레이스 매니페스트
├── agents/                      ← Claude Code 서브에이전트
│   ├── capcut/AGENT.md
│   └── subtitle-verifier/AGENT.md
├── skills/                      ← Claude Code 스킬 (목적별 분리)
│   ├── vibecut-add-subtitles/SKILL.md  ← 자막 자동 생성·검증·적용
│   └── vibecut-auto-edit/SKILL.md      ← 무음 제거 컷편집
├── scripts/                     ← uv-ready Python 스크립트
│   ├── add_subtitles.py
│   ├── capcut_editor.py
│   └── make_segments.py
├── data/
│   └── corrections.json         ← 한국어 오인식 사전 (사용할수록 누적)
├── AGENTS.md                    ← Codex CLI / 범용 가이드
├── README.md                    ← 이 파일
├── pyproject.toml               ← uv 의존성
├── LICENSE
└── .gitignore
```

---

## 주요 개념

### 1. CapCut JSON 4개 파일 동시 저장 (필수)

CapCut은 4개 파일을 동시에 검사합니다. 하나라도 빠지면 변경사항 무시:
```
project_dir/draft_info.json
project_dir/draft_info.json.bak
project_dir/Timelines/<UUID>/draft_info.json
project_dir/Timelines/<UUID>/draft_info.json.bak
```

### 2. 단어 순차 매칭 (싱크 정확도)

시간 범위 매칭(`overlap`)은 같은 단어를 두 자막에 중복 할당해 순서 꼬임을 만듭니다.  
대신 **전역 단어 큐 + 인덱스**로 순차 소비:
- 각 단어는 정확히 한 자막에만 할당됨
- 다음 자막의 시작 시간을 넘는 단어는 다음 자막용으로 보존

### 3. 누적 오인식 사전

`data/corrections.json`이 영상마다 자동 업데이트됩니다:

```json
{
  "dictionary": {
    "챕포": "챗봇",
    "재미나": "Gemini",
    "코덱스리그젝": "codex --exec"
  }
}
```

새 영상에서 발견된 오인식은 `subtitle-verifier`가 자동으로 사전에 추가 → 다음 영상에서 즉시 자동 적용.

---

## 사용 예시 — 실제 흐름

### Claude Code

```
사용자: @capcut myvideo.mov에 자막 추가해줘

capcut 에이전트:
[1/3] Whisper 모델 선택 — small / medium / large-v3
[2/3] 자막 검증 → 12건 교정 ("챕포"→"챗봇" 외)
[3/3] CapCut 프로젝트 자동 생성

✓ CapCut을 열어 'myvideo' 프로젝트를 확인하세요
  자막 134개 (단어 단위 동기화, 18자 분리, 검은 외곽선)
```

### Codex CLI

```bash
codex --exec "AGENTS.md 보고 ~/Movies/lecture.mov에 \
  자막을 추가해. Whisper는 medium 모델로."

# Codex가 AGENTS.md를 읽고 scripts/add_subtitles.py 호출
# → lecture.srt, lecture_verified.srt 생성
# → CapCut 프로젝트 자동 생성
```

---

## 라이선스

MIT License — 자유롭게 사용·수정·배포 가능.

## 기여

이슈/PR 환영: https://github.com/AX-Surfers/Vibecut

### 사전(corrections.json) 기여

새로운 한국어 오인식 패턴을 발견하시면 PR로 추가해주세요. 다른 사용자들에게도 도움이 됩니다.

---

> Made with 🎬 by [AX-Surfers](https://github.com/AX-Surfers)
