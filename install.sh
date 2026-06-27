#!/usr/bin/env bash
# Vibecut one-line installer for Claude Code
# Usage: curl -fsSL https://raw.githubusercontent.com/AX-Surfers/Vibecut/main/install.sh | bash

set -euo pipefail

REPO="AX-Surfers/Vibecut"
INSTALL_DIR="${HOME}/.claude/plugins/marketplaces/Vibecut"
MARKETPLACE_JSON="${HOME}/.claude/plugins/known_marketplaces.json"

# ── helpers ──────────────────────────────────────────────────────────────────

info()  { printf '\033[0;34m[vibecut]\033[0m %s\n' "$*"; }
ok()    { printf '\033[0;32m[vibecut]\033[0m %s\n' "$*"; }
warn()  { printf '\033[0;33m[vibecut]\033[0m %s\n' "$*"; }
die()   { printf '\033[0;31m[vibecut] ERROR:\033[0m %s\n' "$*" >&2; exit 1; }

# ── prerequisite check ───────────────────────────────────────────────────────

command -v git >/dev/null 2>&1 || die "git이 설치되어 있지 않습니다."

if ! command -v uv >/dev/null 2>&1; then
    warn "uv가 없습니다. 자동으로 설치합니다..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="${HOME}/.local/bin:${PATH}"
fi

# ── clone or update ───────────────────────────────────────────────────────────

mkdir -p "${HOME}/.claude/plugins/marketplaces"

if [ -d "${INSTALL_DIR}/.git" ]; then
    info "이미 클론되어 있습니다. 최신 버전으로 업데이트..."
    git -C "${INSTALL_DIR}" pull --ff-only
else
    info "Vibecut 저장소를 클론합니다..."
    git clone "https://github.com/${REPO}.git" "${INSTALL_DIR}"
fi

# ── register marketplace ──────────────────────────────────────────────────────

NOW="$(date -u +"%Y-%m-%dT%H:%M:%S.000Z")"

if [ ! -f "${MARKETPLACE_JSON}" ]; then
    echo '{}' > "${MARKETPLACE_JSON}"
fi

# python3으로 JSON 병합 (jq 없이도 동작)
python3 - <<EOF
import json, pathlib, sys

path = pathlib.Path('${MARKETPLACE_JSON}')
data = json.loads(path.read_text()) if path.stat().st_size else {}

data['Vibecut'] = {
    'source': {'source': 'github', 'repo': '${REPO}'},
    'installLocation': '${INSTALL_DIR}',
    'lastUpdated': '${NOW}',
}

path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n')
print('  known_marketplaces.json 업데이트 완료')
EOF

# ── create official-compatible directory structure ────────────────────────────
# Claude Code는 marketplaces/<name>/plugins/<plugin>/skills/ 구조를 기대합니다.
# Vibecut은 skills/이 루트에 있으므로 심볼릭 링크로 연결합니다.

PLUGIN_DIR="${INSTALL_DIR}/plugins/vibecut"
mkdir -p "${PLUGIN_DIR}"

[ -L "${PLUGIN_DIR}/plugin.json" ] || \
    ln -sf "${INSTALL_DIR}/.claude-plugin/plugin.json" "${PLUGIN_DIR}/plugin.json"
[ -L "${PLUGIN_DIR}/skills" ] || \
    ln -sf "${INSTALL_DIR}/skills" "${PLUGIN_DIR}/skills"
[ -L "${PLUGIN_DIR}/agents" ] || \
    ln -sf "${INSTALL_DIR}/agents" "${PLUGIN_DIR}/agents"

# ── done ─────────────────────────────────────────────────────────────────────

ok "설치 완료!"
echo ""
echo "  사용 가능한 스킬:"
echo "    /vibecut-add-subtitles    — Whisper 한국어 자막 자동 생성"
echo "    /vibecut-auto-edit        — 무음 제거 · NG 감지 컷편집"
echo "    /vibecut-youtube-description — 유튜브 제목·설명·챕터 생성"
echo ""
echo "  Claude Code 인터랙티브 터미널에서 플러그인을 활성화하려면:"
echo "    /plugin install vibecut@Vibecut"
echo ""
