---
name: vibecut-setup
version: 0.1.0
description: |
  Vibecut 환경 초기화. uv·ffmpeg 확인, Python 의존성 사전 설치, 스크립트 경로 자동 감지 후 저장.
  install.sh 없이 Claude Code에서 직접 실행. 플러그인 설치 후 처음 한 번만 실행하면 됨.
  트리거: "vibecut 설정", "vibecut 초기화", "vibecut setup", "/vibecut-setup"
metadata:
  category: video
  locale: ko-KR
allowed-tools:
  - Bash
  - Write
  - AskUserQuestion
---

# vibecut-setup 스킬

플러그인 설치 후 **한 번만** 실행하면 이후 모든 스킬이 별도 설정 없이 동작합니다.
실제 진단 로직(uv/ffmpeg 확인, CapCut 경로 탐지, 의존성 설치, config 저장)은
`scripts/doctor.py`에 있습니다 — macOS·Windows 모두 순수 Python으로 동작합니다.
이 스킬은 그 `doctor.py`를 어떤 설치 경로(Claude Code 플러그인, Codex CLI,
수동 클론)에서도 찾아서 실행하는 역할만 합니다.

## 처리 흐름

```
[1] scripts 디렉토리 확보 (config → 플러그인 캐시 → git clone 순으로 탐색)
[2] uv run scripts/doctor.py 실행
      ├─ uv 설치 확인 → 없으면 안내
      ├─ ffmpeg 확인 → 없으면 설치 안내 (경고만, 중단하지 않음)
      ├─ 플랫폼별 CapCut 프로젝트 디렉토리 탐지
      ├─ uv sync — Python 의존성 사전 설치
      └─ ~/.vibecut/config.json 저장 (scripts_dir, platform, capcut_projects_dir)
[3] 완료 보고
```

## 실행 절차

### 1단계: scripts 디렉토리 확보

```bash
VIBECUT_CONFIG="${HOME}/.vibecut/config.json"
SCRIPTS=""
if [ -f "${VIBECUT_CONFIG}" ]; then
  SCRIPTS=$(python3 -c "import json; print(json.load(open('${VIBECUT_CONFIG}')).get('scripts_dir',''))" 2>/dev/null)
fi
if [ -z "${SCRIPTS}" ]; then
  # Claude Code 플러그인으로 설치된 경우 — 플러그인 캐시에 scripts/가 함께 딸려옴
  SCRIPTS=$(find "${HOME}/.claude/plugins/cache/vibecut" -name "capcut_editor.py" -maxdepth 8 2>/dev/null | head -1 | xargs dirname 2>/dev/null)
fi
if [ -z "${SCRIPTS}" ]; then
  # Codex CLI 등 스킬 폴더만 설치된 경우 — 저장소를 직접 받아 scripts/를 확보
  APP_DIR="${HOME}/.vibecut/app"
  echo "Vibecut 저장소를 ${APP_DIR}에 내려받습니다..."
  if [ -d "${APP_DIR}/.git" ]; then
    git -C "${APP_DIR}" pull --ff-only
  else
    git clone --depth 1 https://github.com/AX-Surfers/Vibecut.git "${APP_DIR}"
  fi
  [ -f "${APP_DIR}/scripts/capcut_editor.py" ] && SCRIPTS="${APP_DIR}/scripts"
fi

if [ -z "${SCRIPTS}" ]; then
  echo "❌ scripts 디렉토리를 찾을 수 없습니다."
  echo "   플러그인이 설치됐는지 확인하세요: /plugin install vibecut@vibecut"
  echo "   또는 git이 설치되어 있는지 확인하세요."
  exit 1
fi
echo "✅ scripts 경로: ${SCRIPTS}"
```

### 2단계: doctor.py 실행

```bash
uv run "${SCRIPTS}/doctor.py"
```

이 한 번의 호출로 uv/ffmpeg 확인, 플랫폼별 CapCut 프로젝트 디렉토리 탐지,
`uv sync`, `~/.vibecut/config.json` 저장이 모두 처리됩니다. uv가 아직 없다면
먼저 설치를 안내합니다:

```bash
which uv || curl -LsSf https://astral.sh/uv/install.sh | sh   # macOS/Linux
# Windows: powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

### 3단계: 완료 보고

`doctor.py`가 다음과 같은 요약을 출력하면 설정이 끝난 것입니다:

```
✅ Vibecut 설정 완료!

사용 가능한 스킬:
  /vibecut-auto-edit           — 무음 제거 · NG 감지 컷편집
  /vibecut-add-subtitles       — Whisper 한국어 자막 자동 생성
  /vibecut-youtube-description — 유튜브 제목·설명·챕터 생성
```

## 환경변수 (선택)

| 변수 | 용도 |
|------|------|
| `VIBECUT_CAPCUT_DIR` | CapCut 프로젝트 루트 경로를 직접 지정 (자동 탐지 결과가 틀릴 때) |
| `VIBECUT_TEMPLATE_NAME` | 템플릿으로 쓸 CapCut 프로젝트 이름 지정 (기본: 자동 감지) |

## 재실행 (업데이트 후)

플러그인 업데이트(`/plugin update vibecut`) 후, 또는 `~/.vibecut/app`을 최신화한
후 재실행하면 새 scripts 경로/설정으로 자동 갱신됩니다.
