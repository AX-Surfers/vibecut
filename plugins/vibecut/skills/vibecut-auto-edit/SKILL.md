---
name: vibecut-auto-edit
version: 0.10.0
description: |
  Whisper 전사 → 정적 표시된 transcript를 Claude가 직접 읽고 NG 판단 → 사용자 검토 →
  CapCut 컷 적용 → 자막까지 자동 연결. 영상이 들어 있는 CapCut 프로젝트·타임라인을
  자동으로 찾고, 타임라인이 여러 개인 프로젝트와 영상 여러 개도 처리한다.
  강의형 콘텐츠의 "강조용 의도적 반복"을 실수로 오판하지 않도록 컷 적용 전
  번호 리스트로 NG 후보를 보여주고 승인/제외를 받는다.
  사용자가 손으로 다듬은 앞부분의 컷 스타일을 재서 뒷부분에 적용하고, 얼굴캠(DJI 등)을
  소리로 싱크 맞춰 PIP로 올린다.
  트리거: "무음 제거", "컷편집", "캡컷 편집", "NG 제거", "뒷부분만 이어서", "얼굴캠", "DJI 영상 넣어",
  "/vibecut-auto-edit"
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

# vibecut-auto-edit 스킬

**영상 → Whisper 전사 → 정적 표시 transcript → Claude NG 판단 → 사용자 검토 → CapCut 적용 → 자막** 파이프라인.

## 핵심 원리

Whisper가 전사한 단어 타임스탬프와 실제 오디오의 정적 구간을 함께 펼친 transcript를
**Claude가 직접 읽고** NG 구간을 판단합니다.

- 키워드/유사도 방식은 문장 *내부* 반복, 3~4회에 걸친 점진적 반복, 불완전 발화를 못 잡음
- Whisper 대본만 읽으면 **말이 멈춘 정적**을 못 봄 — Whisper가 멈춘 시간을 앞 단어 길이에
  흡수시키기 때문. 그래서 `make_transcript.py`가 ffmpeg로 정적을 재서 `⏸` 표시를 끼워 넣음

## 처리 흐름

```
영상 (.mov/.mp4)                     ※ 모든 중간 파일은 영상 옆에 {stem}_*.json 으로 저장
   │                                   (영상이 여러 개여도 섞이지 않고, 캐시 재사용도 안전)
   ├─ [0] 프로젝트·타임라인 찾기 (find_project.py) — 사용자에게 묻지 않고 자동 탐색
   ├─ [1] transcribe.py            → {stem}_words.json
   ├─ [2] make_transcript.py       → {stem}_transcript.txt (⏸ 정적 / 🔊 인식 안 된 소리 표시)
   │      Claude가 읽고 NG 판단   → {stem}_ng_log.json
   ├─ [2-D] 사용자 사전 검토        번호 리스트 → 승인/제외 반영
   ├─ [3] make_segments.py         → {stem}_segments.json
   ├─ [3-B] 프로젝트 상태 점검
   ├─ [4] capcut_editor.py --timeline <이름>   4개 파일 갱신 + 자동 백업
   ├─ [5] 자막 자동 연결            subtitles_from_cuts.py → 분할 → apply_subtitles.py
   ├─ [6] vibecut app으로 열기
   │
   ├─ (요청 시) 손편집 스타일로 뒷부분 촘촘히   tighten_pauses.py   ← "앞은 내가 했어, 뒷부분 이어서"
   └─ (요청 시) 얼굴캠 PIP                     facecam.py          ← "DJI 영상 싱크 맞춰 오른쪽 아래에"
```

## 전제 조건 (모든 Bash 블록 앞에 한 번)

```bash
which uv || curl -LsSf https://astral.sh/uv/install.sh | sh

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

uv run python -c "import sys; sys.path.insert(0, '${SCRIPTS}'); from _platform import check_capcut_not_running as c; c()"
```

## 실행 흐름

### 단계 0: CapCut 프로젝트·타임라인 찾기

사용자가 프로젝트 이름을 말하지 않았으면 **묻지 말고 먼저 찾습니다.** 영상 파일명으로
모든 프로젝트의 소재를 대조합니다.

```bash
VIDEO="<영상 파일 경로>"
uv run "${SCRIPTS}/find_project.py" "${VIDEO}"
# 출력: <프로젝트 경로>\t<타임라인 이름>\t<타임라인 id>  (최근 수정 순)
```

- 1곳이면 그대로 `PROJECT`, `TIMELINE`으로 씁니다.
- 여러 곳이면 가장 최근 것을 제안하며 사용자에게 확인합니다.
- 0곳이면 CapCut에서 영상을 먼저 가져오도록 안내합니다 (미디어 가져오기는 GUI 작업이라 자동화하지 않음).
- 사용자가 "0903 프로젝트"처럼 이름을 주면 `uv run "${SCRIPTS}/find_project.py" --recent 10`으로 경로와 타임라인 목록을 확인합니다.

```bash
PROJECT="<CapCut 프로젝트 경로>"
TIMELINE="<타임라인 이름>"   # 예: "타임라인 01". 타임라인이 1개면 빈 문자열
TL_OPT=(); [ -n "${TIMELINE}" ] && TL_OPT=(--timeline "${TIMELINE}")
```

**타임라인이 여러 개인 프로젝트**(CapCut 하단 탭 "타임라인 01/02")는 타임라인마다 영상이
다릅니다. 각 타임라인 = 별개의 편집 작업이므로 아래 단계 1~5를 **타임라인별로 반복**합니다.
`--timeline`을 빼면 스크립트가 목록을 보여주고 멈추므로, 엉뚱한 타임라인을 편집할 일은 없습니다.

### 단계 1: Whisper 전사

```bash
WHISPER_MODEL="large-v3-turbo"      # 기본 고정, 질문 생략. 사용자가 이번만 다른 모델을 말하면 그 값
STEM="${VIDEO%.*}"
uv run "${SCRIPTS}/transcribe.py" "${VIDEO}" --model "${WHISPER_MODEL}"
# → {STEM}_words.json (있으면 재사용), {STEM}_audio.wav
```

출력 끝의 `⚠` 줄을 읽습니다 — "N초 동안 인식된 말이 없음", "분당 단어 수 30개 미만"이 뜨면
모델이 구간을 통째로 놓친 것일 수 있으니 사용자에게 알리고 진행 여부를 확인합니다.

⚠ **커뮤니티 한국어 fine-tune 모델은 기본으로 쓰지 않습니다.** 실전에서
`ghost613/faster-whisper-large-v3-turbo-korean`은 52분 중 51분을 통째로 누락했고,
`seastar105/whisper-medium-komixv2`는 단어 밀도가 낮아 파편 클립을 양산했습니다.
사용자가 명시적으로 요청하면: (1) `curl -s -o /dev/null -w "%{http_code}" https://huggingface.co/<repo>`로
존재 확인 (모델명을 지어내지 말 것), (2) CTranslate2 형식이 아니면
`uv run --with ctranslate2 --with "transformers[sentencepiece]" --with torch ct2-transformers-converter --model <repo> --output_dir <dir> --quantization int8`,
(3) 전사 후 위 `⚠` 점검을 반드시 통과시킬 것.

### 단계 2: transcript 생성 + Claude NG 분석

#### 2-A: 정적이 표시된 transcript

```bash
uv run "${SCRIPTS}/make_transcript.py" "${STEM}_words.json"
# → {STEM}_transcript.txt 저장 + 화면 출력
```

출력 형식 — 각 줄 = 한 번의 시도(take), 정적은 별도 줄:

```
[00:36 (36.4~38.9s)] 개발자 멘토가 옆에서 직접 봐드립니다
[00:38 (38.9~39.0s)] 한
    ⏸ 정적 4.1초 (39.0~43.1s)
[00:43 (43.2~45.7s)] 달 20시간의 과정을 마치고 나면
    🔊 인식 안 된 소리 22.9초 (25.0~48.0s) — 정적 아님, NG 잡담일 수 있음
```

- `⏸ 정적` — 실제 오디오가 조용한 구간. 1초 이상이면 대개 NG(말 멈춤·호흡)입니다.
- `🔊 인식 안 된 소리` — 소리는 있는데 Whisper가 글자로 못 옮긴 구간. 실전에서 이 자리에
  "다시 처음부터 할게요" 같은 NG 잡담이 있었습니다. 앞뒤 문맥으로 NG인지 판단하고,
  애매하면 사용자에게 그 구간을 확인 요청합니다.

#### 2-B: Claude가 transcript 전체를 읽고 NG 판단

`{STEM}_transcript.txt`를 **전체 읽은 후** 판단합니다.

| 패턴 | 예시 | 판단 방법 |
|------|------|----------|
| 복수 시도 | 인접 줄이 같거나 비슷한 내용 | 마지막 시도만 남기고 앞의 것 모두 NG |
| 명시적 NG 신호 | "잠깐", "다시", "아니", "죄송", 카메라/스태프에게 하는 말 | 해당 줄 + 직전 줄까지 NG |
| 불완전 발화 | 문장이 중간에 끊기고 재시작 | 짧고 의미 없는 단편 줄 |
| 정적 | `⏸` 1초 이상 | 정적 구간 자체를 NG (앞뒤 말은 살림) |
| 인식 안 된 소리 | `🔊` | 문맥상 NG 잡담이면 NG, 애매하면 사용자 확인 |

- NG 신호어("다시", "잠깐")가 있으면 **신호어 이전** 발화까지 포함
- 복수 시도는 **마지막 시도만** 남김 (강의형 콘텐츠의 의도적 강조 반복은 2-D에서 사용자가 걸러줌)
- 정적 NG의 경계는 `⏸` 줄의 시각을 그대로 씁니다 — 앞 단어 끝을 잘라먹지 않습니다

#### 2-C: ng_log.json 작성

```bash
uv run python - "${STEM}_ng_log.json" <<'PYEOF'
import json, sys
ng_spans = [
    # [시작_초, 끝_초] — transcript의 시각 기준
    # 예: [29.6, 33.8],   # "설명하고 눈 괜찮았어요?" 촬영 중 딴소리
    # 예: [39.0, 43.1],   # "한" 뒤 정적 4.1초
]
json.dump({"ng_spans": ng_spans}, open(sys.argv[1], "w"), ensure_ascii=False, indent=2)
print(f"NG 구간: {len(ng_spans)}개, 총 {sum(e-s for s,e in ng_spans):.1f}초 → {sys.argv[1]}")
PYEOF
```

> heredoc은 `<<'PYEOF'`(따옴표)라 안에서 `${STEM}`이 안 풀립니다. 경로는 위처럼
> **인자로 넘기세요.** (예전 스킬은 이 실수로 파일 없음 오류가 났습니다)

### 단계 2-D: 사용자 사전 검토 (컷 적용 전 필수)

강의형 콘텐츠는 강조를 위해 같은 말을 일부러 반복하는 경우가 많아, "복수 시도" 판정이
의도적 반복을 잘라내는 오탐을 낼 수 있습니다. **컷 적용 전에** 번호 리스트로 보여주고
승인/제외를 받습니다. 이 단계는 생략하지 않습니다.

```bash
uv run python - "${STEM}_transcript.txt" "${STEM}_ng_log.json" <<'PYEOF'
import json, re, sys
lines = open(sys.argv[1], encoding="utf-8").read().splitlines()
spans = json.load(open(sys.argv[2], encoding="utf-8"))["ng_spans"]
rx = re.compile(r"\((\d+\.?\d*)~(\d+\.?\d*)s\)\]?\s*(.*)")
parsed = [(float(m.group(1)), float(m.group(2)), m.group(3)) for m in map(rx.search, lines) if m]
for i, (s, e) in enumerate(spans, 1):
    hit = " / ".join(t for ls, le, t in parsed if ls < e and le > s)[:80] or "(정적)"
    m, sec = divmod(int(s), 60)
    print(f"[{i}] {m:02d}:{sec:02d} ({e-s:.1f}초) — \"{hit}\"")
PYEOF
```

리스트를 보여준 뒤 `AskUserQuestion`으로 묻습니다:

```python
AskUserQuestion(questions=[{
    "question": "위 N개 구간을 NG로 판단했습니다. 어떻게 할까요? (일부만 빼려면 '기타'에 번호를: 예 '2, 7번 빼고')",
    "header": "NG 검토",
    "multiSelect": False,
    "options": [
        {"label": "전체 적용 (Recommended)", "description": "N개 구간을 모두 잘라냅니다"},
        {"label": "다시 분석", "description": "놓친 곳이나 잘못 잡은 곳을 알려주시면 재분석합니다"},
    ],
}])
```

제외 번호를 받으면 `spans`에서 빼고 `{STEM}_ng_log.json`을 다시 씁니다. "다시 분석"이면
사용자 피드백을 반영해 2-B부터 다시 합니다.

### 단계 3: 클립 구간 생성

```bash
uv run "${SCRIPTS}/make_segments.py" --words-json "${STEM}_words.json" --ng "${STEM}_ng_log.json"
# → {STEM}_segments.json
```

- Whisper 세그먼트(문장) 전체를 클립 경계로 쓰고, NG로 찍은 곳만 정확히 잘라냅니다.
  (예전 "NG가 문장의 50% 이상이면 문장을 통째로 버림" 규칙은 살려야 할 재녹음 테이크까지
  날려서 제거됨 — 기본 임계값 100%)
- 클립 시작 패딩 0.12초, 끝 패딩 0.2초 기본. 앞글자/종결어미가 잘리면 `--pad-start 0.18`,
  `--pad-end 0.3`으로 조정. 시작 패딩은 끝 패딩보다 작게 유지(직전 클립 꼬리와 겹치면 말이 중복됨).
- `--word-split`은 단어 인식 밀도가 검증된 경우에만 (파편 클립 양산 실패 사례 있음).

### 단계 3-B: 적용 전 프로젝트 상태 점검 (필수)

컷을 적용하면 대상 트랙의 세그먼트가 **통째로 교체**됩니다. 적용 직전에 현재 구성을 확인합니다.

```bash
uv run python - "${PROJECT}" "${TIMELINE}" "${SCRIPTS}" <<'PYEOF'
import json, sys
from collections import Counter
from pathlib import Path
project, timeline, scripts = sys.argv[1:4]
sys.path.insert(0, scripts)
from capcut_editor import draft_path_for, resolve_timeline
proj = Path(project)
tl, _ = resolve_timeline(proj, timeline or None)
d = json.loads(draft_path_for(proj, tl).read_text(encoding="utf-8"))
mats = {m["id"]: m.get("path", "") for m in d["materials"].get("videos", [])}
print(f"전체 길이: {d['duration']/1e6:.1f}초")
for i, t in enumerate(d["tracks"]):
    names = Counter(Path(mats.get(s.get("material_id"), "?")).name for s in t.get("segments", []))
    print(f"  tracks[{i}] {t['type']} '{t.get('name', '')}': {len(t.get('segments', []))}개 {dict(names)}")
PYEOF
```

- **비디오 트랙이 2개 이상**이면 어느 쪽이 원본 영상인지 확인합니다. 배경 이미지·오버레이가
  올라가 있으면 `--track N`으로 대상을 명시합니다.
- **세그먼트 수·전체 길이가 예상과 다르면** 그 사이 사용자가 편집했거나 이전 작업이 꼬인 것
  입니다. 덮어쓰기 전에 사용자에게 확인합니다.

### 단계 4: CapCut JSON 적용

```bash
uv run python -c "import sys; sys.path.insert(0, '${SCRIPTS}'); from _platform import check_capcut_not_running as c; c()"
uv run "${SCRIPTS}/capcut_editor.py" "${STEM}_segments.json" --project "${PROJECT}" "${TL_OPT[@]}"
```

로그에서 `🗂 타임라인`, `🎬 영상 1: <파일명>`, 감소율을 **반드시 읽습니다.** 파일명이 다르거나
`원본 NNN분`이 비상식적으로 크면 즉시 중단하고 백업에서 되돌립니다.

- 실제 영상 파일을 참조하는 트랙을 자동 선택하고, 대상이 이미지면 중단합니다
  (배경 PNG 한 장이 38조각으로 잘린 사고가 있었음). 강제하려면 `--track N`.
- 타임라인이 여러 개면 `--timeline` 없이는 멈춥니다. 메인이 아닌 타임라인은 루트
  `draft_info.json`을 건드리지 않습니다(루트는 메인 타임라인의 사본).
- **되돌리기**: `<프로젝트상위>/.vibecut_backups/<프로젝트명>/<타임스탬프>_capcut_editor.tar.gz`
  → `tar -xzf <파일> -C "${PROJECT}"`

### 단계 5: 자막 자동 연결 — 컷 매핑 방식 (재전사 금지)

사용자가 "자막은 나중에"/"컷편집만"이라고 하지 않은 한 바로 이어서 자막을 올립니다.
**원칙: 자막 경계 == 컷 경계.** 편집 오디오를 다시 전사하지 않습니다 — Whisper가 하드컷을
무시해 자막이 컷을 가로지르고 같은 문장이 두 번 나오던 실패 사례가 있습니다.

```bash
# 5-A 원본 words.json을 컷 매핑으로 옮김 (오인식 사전 corrections.json 자동 적용)
uv run "${SCRIPTS}/subtitles_from_cuts.py" --words "${STEM}_words.json" \
  --project "${PROJECT}" "${TL_OPT[@]}"
# → {STEM}_subtitle_input.json. "✓ 모든 자막이 컷 경계 안에…" 확인. 위반 시 종료코드 1
```

`{STEM}_audio.wav`가 있으면 단어 시각을 실제 말소리에 맞춰 다듬습니다("실제 정적에 맞춰 단어 시각 교정: N개").
Whisper는 멈춘 시간을 앞 단어에 흡수시켜, 말이 끝났는데 자막이 남고 다음 자막이 늦게 뜨던 실패가
있었습니다. 기준은 -45dB — 노이즈 게이트 녹음에서 -30dB로 재자 구절 속 틈까지 잡혀 자막이 0.6초 만에
사라지는 등 더 나빠졌습니다. 글자/초 9를 넘게 줄이는 조정은 하지 않습니다.

5-B 문법 경계 분할 — `subtitle-splitter` 에이전트를 호출합니다 (경로를 명시):

```
{STEM}_subtitle_input.json 을 읽어 한국어 문법 경계로 분할하고
{STEM}_subtitle_splits.json 에 저장해줘. 세그먼트 수 1:1 대응, 각 원소는 텍스트 조각 배열.
```

5-C 적용 — 비디오·오디오·사용자가 넣은 텍스트 트랙은 모두 보존하고, vibecut이 만든
자막 트랙(이름 `vibecut`)만 교체합니다:

```bash
uv run python -c "import sys; sys.path.insert(0, '${SCRIPTS}'); from _platform import check_capcut_not_running as c; c()"
uv run "${SCRIPTS}/apply_subtitles.py" --project "${PROJECT}" "${TL_OPT[@]}" \
  --input "${STEM}_subtitle_input.json" --splits "${STEM}_subtitle_splits.json"
```

예전 버전이 만든 **이름 없는** 자막 트랙이 남아 있으면 로그에 `보존 (교체하려면 --replace-track N)`으로
뜹니다. 자막이 겹쳐 보이지 않게 `--replace-track N`을 붙여 다시 실행합니다.

### 단계 6: vibecut app으로 프로젝트 열기

사용자가 "vibecut app은 나중에"라고 하지 않은 한 방금 편집한 **CapCut 프로젝트**를 앱으로 띄웁니다
(영상 파일로 열지 않습니다 — 영상으로 열면 CapCut 되돌려쓰기 경로가 지워집니다).
`-n`으로 항상 새 인스턴스를 띄웁니다 — 이미 열려 있으면 `open -a`만으로는 새 인자를 못 받습니다.

- **프로젝트 폴더가 아니라 편집한 타임라인의 `draft_info.json`을 넘깁니다.** 폴더를 넘기면 앱이
  `Timelines/` 안에서 처음 보이는 타임라인을 골라, 타임라인이 여러 개면 엉뚱한 쪽이 열립니다.
- **앱이 실행 인자를 지원하는지 먼저 봅니다** (`get_launch_project_path` 명령이 바이너리에 있는지).
  `strings | grep`은 릴리스 바이너리에서 이 이름을 못 찾아 멀쩡한 앱을 옛 빌드로 오판했습니다 —
  반드시 `grep -a`로 원시 바이트를 검색합니다.

```bash
DRAFT=$(uv run python - "${PROJECT}" "${TIMELINE}" "${SCRIPTS}" <<'PYEOF' | tail -1
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[3])
from capcut_editor import draft_path_for, resolve_timeline
tl, _ = resolve_timeline(Path(sys.argv[1]), sys.argv[2] or None)
print(draft_path_for(Path(sys.argv[1]), tl))
PYEOF
)
APP=$(ls -d "/Applications/vibecut app.app" "${HOME}/Applications/vibecut app.app" 2>/dev/null | head -1)
if [ -z "${APP}" ]; then
  echo "vibecut app 미설치 — 건너뜀"
elif ! grep -a -q get_launch_project_path "${APP}/Contents/MacOS/vibecut"; then
  echo "⚠ 설치된 vibecut app이 프로젝트 경로 인자를 지원하지 않는 옛 빌드 — 새로 빌드해야 프로젝트로 열립니다"
else
  open -n -a "${APP}" --args "${DRAFT}"
fi
```

## 부분 재편집 (사용자가 CapCut에서 이미 손으로 다듬은 뒤 나머지만 다시)

**트리거**: "N분까지는 내가 편집했어, 뒷부분만", "앞부분은 그대로 두고 뒤만".

1. **경계를 사용자 말로 어림잡지 말 것.** "1분까지"는 편집본 기준 대략치입니다. 현재
   타임라인의 클립 목록(`splice_segments.py`가 출력하는 보존 클립 끝 시각)을 보고 실제
   원본 기준 경계를 잡습니다. 문장이 끊기지 않도록 경계는 항상 클립 끝에 맞춥니다.
2. 단계 1~3을 다시 실행해 전체 영상 기준 새 `{STEM}_segments.json`을 만듭니다
   (words.json 재사용, NG 분석은 새로 — 보존 구간 이후에서 새로 발견되는 NG가 있을 수 있음).
3. 이어붙이기:
   ```bash
   uv run "${SCRIPTS}/splice_segments.py" --project "${PROJECT}" "${TL_OPT[@]}" \
     --keep-until <원본 기준 경계 초> --new-segments "${STEM}_segments.json"
   # → {STEM}_segments_spliced.json  (--keep-count N 도 가능)
   ```
4. 단계 4와 동일하게 `capcut_editor.py`에 `{STEM}_segments_spliced.json`을 적용합니다.

⚠ 사용자가 CapCut을 열어두고 편집 중일 수 있으니 적용 직전에 CapCut이 꺼져 있는지 확인하고
파일을 **적용 시점에 새로** 읽습니다 — 이전에 읽어둔 값을 쓰면 그 사이 편집을 덮어씁니다.

### 컷은 이미 된 상태에서 "앞은 내가 다듬었어, 뒷부분도 그렇게"

사용자가 CapCut에서 앞부분을 손으로 다듬었으면 **그 편집에서 기준을 재서** 뒷부분에 적용합니다.
실전: 자동 컷은 문장 단위라 클립 안 멈춤이 최대 1.16초 남았고, 사용자는 말 앞 0.06초·뒤 0.04초만 남기고
0.7초 이상 멈춤은 전부, 0.5~0.7초는 대부분 잘랐습니다. NG 판단(말을 자르는 것)은 다시 하지 않습니다.

1. **경계는 백업과 비교해 찾습니다** — 사용자 말("3분까지")은 대략치입니다. `--backup`에 사용자가 편집하기
   직전의 vibecut 백업을 주면, 뒤에서부터 백업과 똑같은 클립을 걷어내 손댄 마지막 클립을 찾습니다.
2. 먼저 `--dry-run --learn`으로 잰 기준·줄어드는 길이·잘린 말소리(0이어야 함)를 보여주고 기준을 확인받습니다.
3. 본영상과 1:1로 맞춰진 얼굴캠 트랙과 경계 뒤 자막은 같이 옮겨집니다. 경계 앞은 건드리지 않습니다.

```bash
BACKUP="<.vibecut_backups/<프로젝트>/<사용자 편집 직전 타임스탬프>_capcut_editor.tar.gz>"
uv run "${SCRIPTS}/tighten_pauses.py" --project "${PROJECT}" "${TL_OPT[@]}" --backup "${BACKUP}" --learn --dry-run
# 확인받은 뒤 --dry-run 없이 (기준을 바꾸려면 --min-pause 0.5 등)
uv run "${SCRIPTS}/tighten_pauses.py" --project "${PROJECT}" "${TL_OPT[@]}" --backup "${BACKUP}" --learn
```

## 얼굴캠(DJI 등) PIP

**트리거**: "DJI 영상도 싱크 맞춰 넣어줘", "얼굴 오른쪽 아래에".

1. 얼굴캠 파일은 사용자가 CapCut에서 가져와 **아무 비디오 트랙에 한 번 올립니다** (미디어 가져오기는 GUI 작업).
2. 크기와 소리는 사용자에게 묻습니다 (크기: 캔버스 높이 대비 비율 / 소리: `auto`는 노이즈 게이트로 끊기는
   쪽을 피함 — 실전에서 화면녹화는 칸의 46%가 완전무음, DJI는 0%였음).
3. 적용 후 출력되는 "싱크 실측 어긋남"이 ±15ms 안인지 봅니다.

```bash
uv run "${SCRIPTS}/facecam.py" --project "${PROJECT}" "${TL_OPT[@]}" --facecam "<DJI 파일명>" \
  --corner br --height 0.35 --margin 0 --audio auto --dry-run
# 확인받은 뒤 --dry-run 없이. 위치·크기만 바꿀 때: --place-only --margin 0
# 사용자가 손으로 다듬은 앞부분이 있으면: --from-clip <첫 자동 편집 클립> (앞은 300ms 넘게 어긋난 조각만 고침)
```

- **싱크는 소리로 잽니다.** 파일 생성 시각 차이(42초)는 틀렸고 실제로는 1.4초였습니다.
- **두 기기의 시계는 실제로 다르게 갑니다.** DJI가 약 40ppm 느려 37분 동안 오프셋이 1.43→1.34초로 줄었습니다.
  스크립트는 녹화 곳곳 8개 창에서 양쪽 ±0.3초로 찾고 직선으로 맞춥니다. 직선에서 25ms 넘게 벗어난 창이 있으면
  (녹화 끊김 의심) 멈추고 알립니다.
- **탐색 범위를 한쪽으로만 잡지 않습니다.** 1.40~1.60초만 찾았다가 결과가 전부 아래 끝(1.400)에 붙어
  "오프셋이 일정하다"고 잘못 결론 내리고 멀쩡한 시계 차이 보정을 없앤 적이 있습니다. 결과가 탐색 끝에 붙으면 버립니다.
- **CapCut은 원본 시작점을 30fps 칸에 맞춰 저장합니다.** 한 프레임(33ms)보다 정밀한 싱크는 불가능하니,
  실측 어긋남이 ±17ms 안이면 정상입니다.
- 오른쪽 아래 모서리를 영상 모서리에 딱 붙이는 게 기본(`--margin 0`)입니다 — 30px 띄웠다가 붙여 달라는 요청을 받았습니다.

## 캐시 (모두 영상 옆, 영상별 분리)

| 파일 | 있으면 |
|------|--------|
| `{stem}_words.json` | 전사 생략 |
| `{stem}_transcript.txt` | 그래도 다시 생성 (1초, 정적 측정 포함) |
| `{stem}_ng_log.json` | "NG 다시 분석" 요청이 없으면 재사용 → 2-D 검토부터 |
| `{stem}_segments.json` | "구간 생성만 다시"가 아니면 재생성 (싸다) |

`/tmp/ng_log.json` 같은 **고정 이름의 옛 파일은 절대 재사용하지 않습니다** — 다른 영상의
NG 구간을 그대로 자를 수 있습니다 (실제로 다른 영상 것이 남아 있었음).

## 사용자 호출 예시

| 사용자 발화 | 동작 |
|------------|------|
| "컷편집해줘" / "1.MP4 컷편집" | 전체 파이프라인 (프로젝트 자동 탐색 → … → 자막 → vibecut app) |
| "0903 프로젝트 타임라인1, 타임라인2 둘 다 컷편집" | 타임라인별로 단계 1~5 반복 (영상마다 `{stem}_*` 파일) |
| "NG만 다시 분석해줘" / "NG 구간이 더 있어" | words.json 재사용 → transcript 다시 읽고(특히 ⏸/🔊 줄) → ng_log 재작성 → 2-D |
| "구간 생성만 다시 해줘" | make_segments.py만 |
| "컷편집만 해줘 (자막은 나중에)" | 단계 5 생략 |
| "vibecut app은 나중에" | 단계 6 생략 |
| "N분까지는 편집했어, 뒷부분만" | 부분 재편집 절차 |
| "앞은 내가 다듬었어, 내가 한 거 반영해서 뒷부분 진행해" | `tighten_pauses.py --backup … --learn` (NG 재분석 없음) |
| "DJI 영상 싱크 맞춰서 오른쪽 아래에 넣어줘" | `facecam.py` (크기·소리 확인 후) |
| "DJI 모서리를 영상 모서리에 맞춰줘" | `facecam.py --place-only --margin 0` |

## 주의사항

- **CapCut이 꺼져 있어야 씁니다 — 강제 종료하지 않습니다.** 사용자가 CapCut으로 같이 편집하는 중일 수 있어
  `pkill`로 끄면 저장 안 된 편집이 날아갑니다. 실행 중이면 쓰기 전에 멈추고 "저장 후 Cmd+Q로 종료"를 부탁한 뒤
  기다립니다. 종료 후 파일을 **새로 읽어** 분석 때와 클립·자막 수가 같은지 확인하고 씁니다.
- **NG 리스트 검토가 1차 방어선** — "N번은 왜 뺐어?" 같은 피드백은 다음 분석에 반영합니다.
- **긴 영상(30분+)** — transcript를 10분 단위로 나눠 읽되 NG 판단은 전체 문맥으로.
- **적용 로그를 읽고 넘어갈 것** — `🗂 타임라인`, `🎬 영상 1: <파일명>`, 감소율.

### ⚠ CapCut GUI를 직접 조작하지 말 것 (실전 실패 사례)

이 스킬은 CapCut을 **종료한 상태에서 JSON을 직접 고치는** 방식입니다. 화면 자동화로
CapCut UI를 조작하면 실패가 잦고, 되돌리려 누른 `Cmd+Z`가 **사용자의 기존 편집까지
되돌립니다** (영상 42컷 → 34컷, 자막 173개 → 130개 소실 사고). 미디어 가져오기·크로마키·
클립 배치처럼 GUI가 필요한 작업은 사용자에게 절차를 안내하고 맡깁니다.

### ⚠ 재전사 폴백 (원본 words.json이 없는 프로젝트에서만)

외부에서 편집된 프로젝트는 `vibecut-add-subtitles` 모드 B 폴백을 씁니다. 그때
`ffmpeg -f concat`의 `inpoint/outpoint`는 구간당 약 0.4초씩 초과 추출되어 38컷에서
16.8초가 밀렸으니, 반드시 그 스킬의 `-ss/-t` 개별 추출 + 길이 검증을 지킵니다.
