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

## 처리 흐름

```
[1] uv 설치 확인 → 없으면 자동 설치
[2] ffmpeg 확인 → 없으면 설치 안내 (경고만, 중단하지 않음)
[3] 플러그인 캐시에서 scripts 디렉토리 자동 탐색
[4] uv sync — Python 의존성 사전 설치
[5] ~/.vibecut/config.json 저장
[6] 완료 보고
```

## 실행 절차

### 1단계: uv 확인 및 설치

```bash
if ! command -v uv >/dev/null 2>&1; then
  echo "uv가 없습니다. 자동 설치합니다..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="${HOME}/.local/bin:${PATH}"
  echo "✅ uv 설치 완료: $(uv --version)"
else
  echo "✅ uv: $(uv --version)"
fi
```

### 2단계: ffmpeg 확인

```bash
if command -v ffmpeg >/dev/null 2>&1; then
  echo "✅ ffmpeg: $(ffmpeg -version 2>&1 | head -1)"
else
  echo "⚠️  ffmpeg 없음 — 자막 추가(모드 B, 편집 타임라인 기준)에 필요합니다."
  echo "   설치: brew install ffmpeg"
fi
```

ffmpeg가 없어도 무음 제거·NG 제거(vibecut-auto-edit)는 정상 동작합니다.

### 3단계: scripts 디렉토리 자동 탐색

플러그인 캐시(`~/.claude/plugins/cache/vibecut`)에서 `capcut_editor.py`를 탐색해 경로를 결정합니다.

```bash
SCRIPTS=$(find "${HOME}/.claude/plugins/cache/vibecut" \
  -name "capcut_editor.py" -maxdepth 8 2>/dev/null \
  | head -1 | xargs dirname 2>/dev/null)

if [ -z "${SCRIPTS}" ]; then
  echo "❌ scripts 디렉토리를 찾을 수 없습니다."
  echo "   플러그인이 설치됐는지 확인하세요: /plugin install vibecut@vibecut"
  exit 1
fi
echo "✅ scripts 경로: ${SCRIPTS}"
```

### 4단계: Python 의존성 사전 설치

scripts 상위 디렉토리(플러그인 루트)에서 `uv sync`를 실행해 whisper 등 의존성을 미리 설치합니다.

```bash
PLUGIN_DIR=$(dirname "${SCRIPTS}")
echo "의존성 설치 중... (처음엔 1~2분 소요)"
cd "${PLUGIN_DIR}" && uv sync 2>&1 | tail -5
echo "✅ Python 의존성 설치 완료"
```

### 5단계: config 저장

```bash
mkdir -p "${HOME}/.vibecut"
python3 - <<'PYEOF'
import json, os, pathlib

scripts = os.environ.get('SCRIPTS') or ''
cfg_path = pathlib.Path.home() / '.vibecut' / 'config.json'

existing = {}
if cfg_path.exists():
    try:
        existing = json.loads(cfg_path.read_text())
    except Exception:
        pass

existing['scripts_dir'] = scripts
cfg_path.write_text(json.dumps(existing, indent=2, ensure_ascii=False) + '\n')
print(f"✅ config 저장 완료: {cfg_path}")
PYEOF
```

`SCRIPTS` 환경변수를 python 서브셸에 전달하려면 3단계에서 `export SCRIPTS="${SCRIPTS}"`를 먼저 실행합니다.

### 6단계: 완료 보고

```
✅ Vibecut 설정 완료!

사용 가능한 스킬:
  /vibecut-auto-edit           — 무음 제거 · NG 감지 컷편집
  /vibecut-add-subtitles       — Whisper 한국어 자막 자동 생성
  /vibecut-youtube-description — 유튜브 제목·설명·챕터 생성

설정 파일: ~/.vibecut/config.json
```

## 재실행 (업데이트 후)

플러그인 업데이트(`/plugin update vibecut`) 후 재실행하면 새 scripts 경로로 자동 갱신됩니다.
