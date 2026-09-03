---
name: vibecut-youtube-description
version: 1.1.0
description: |
  CapCut 프로젝트의 자막을 읽어 유튜브 제목·설명·챕터를 자동 생성합니다.
  자막 타임스탬프 기반으로 챕터를 추출하고, 한국어 유튜브 설명 스타일에 맞게 작성합니다.
  결과는 youtube_description.txt 파일로 저장합니다.
  트리거: "유튜브 제목", "유튜브 설명", "설명 작성", "챕터 만들어", "/vibecut-youtube-description"
metadata:
  category: video
  locale: ko-KR
allowed-tools:
  - Bash
  - Read
  - Write
  - AskUserQuestion
---

# vibecut-youtube-description 스킬

**CapCut 자막 → 영상 흐름 파악 → 유튜브 제목 + 설명 + 챕터 자동 생성** 파이프라인.

## 핵심 처리 흐름

```
CapCut draft_info.json
   ├─ [1] 프로젝트·타임라인 결정 → 전체 자막 추출 (타임스탬프 + 텍스트)
   ├─ [2] 영상 정보 확인 (GitHub 링크, 커뮤니티 링크 등) — config 있으면 자동, 없으면 질문
   ├─ [3] 제목 3가지 생성
   ├─ [4] 설명 생성 (후크 → 요약 → 링크 → 📌 다루는 내용 → ⏱️ CHAPTERS)
   └─ [5] youtube_description.txt 저장
```

## 실행 절차

### 1단계: 프로젝트 결정 + 자막 추출

경로는 하드코딩하지 않고 설정(`~/.vibecut/config.json`의 `capcut_projects_dir`, 또는
`VIBECUT_CAPCUT_DIR`)에서 읽습니다. 프로젝트 이름을 모르면 최근 프로젝트를 나열합니다.

```bash
SCRIPTS=$(python3 -c "import json,os; print(json.load(open(os.path.expanduser('~/.vibecut/config.json'))).get('scripts_dir',''))")
uv run "${SCRIPTS}/find_project.py" --recent 5      # <프로젝트 경로>\t<타임라인 이름들>
PROJECT="<CapCut 프로젝트 경로>"
TIMELINE="<타임라인 이름 또는 빈 문자열>"           # 타임라인이 여러 개면 지정 필수
```

```bash
uv run python - "${PROJECT}" "${TIMELINE}" "${SCRIPTS}" <<'PYEOF'
import json, sys
from pathlib import Path
project, timeline, scripts = sys.argv[1:4]
sys.path.insert(0, scripts)
from capcut_editor import draft_path_for, resolve_timeline

proj = Path(project)
tl, _ = resolve_timeline(proj, timeline or None)
draft = json.loads(draft_path_for(proj, tl).read_text(encoding="utf-8"))
mat_map = {t["id"]: t for t in draft["materials"].get("texts", [])}

# 자막 트랙 = type이 text인 트랙 중 세그먼트가 가장 많은 것 (tracks[1] 고정 가정 금지)
text_tracks = [t for t in draft["tracks"] if t.get("type") == "text" and t.get("segments")]
if not text_tracks:
    raise SystemExit("❌ 자막 트랙이 없습니다 — 먼저 vibecut-add-subtitles 를 실행하세요")
track = max(text_tracks, key=lambda t: len(t["segments"]))

subs = []
for seg in sorted(track["segments"], key=lambda s: s["target_timerange"]["start"]):
    mat = mat_map.get(seg["material_id"])
    if not mat:
        continue
    text = json.loads(mat["content"]).get("text", "").strip()
    if text:
        subs.append((seg["target_timerange"]["start"] / 1e6, text))
for st, text in subs:
    m, s = divmod(int(st), 60)
    print(f"  {m:02d}:{s:02d}  {text}")
print(f"\n자막 {len(subs)}줄, 영상 길이 {draft['duration']/1e6/60:.1f}분")
PYEOF
```

### 2단계: 채널 설정 파일 확인

`~/.vibecut/channel_config.json` 또는 프로젝트 루트의 `channel_config.json`을 확인한다.

```json
{
  "github": "https://github.com/AX-Surfers/Vibecut",
  "community": [
    { "label": "카카오톡 오픈채팅", "url": "https://open.kakao.com/o/...", "pw": "..." }
  ],
  "channel_name": "seungryk",
  "default_footer": "github.com/AX-Surfers/Vibecut"
}
```

파일이 없으면 GitHub 링크만 질문한다. (나머지는 선택)

### 3단계: 제목 3가지 생성

자막 전체를 읽고 영상의 핵심 가치를 파악한 뒤 제목을 3가지 생성한다.

**제목 작성 규칙:**
- 40자 이내 (유튜브 검색 최적화)
- 클릭을 유도하는 질문형 또는 결과형
- 핵심 키워드(Claude, AI, CapCut 등) 앞쪽에 배치
- 숫자나 결과가 있으면 포함 ("90% 단축", "완전 자동화")
- 파이프(`|`) 또는 대시(`—`)로 브랜드명 구분

**형식:**
```
[제목 A] 결과 중심형 — "편집 시간 90% 줄였습니다"
[제목 B] 질문형 — "영상 편집 아직 직접 하시나요?"
[제목 C] 방법론형 — "Claude로 컷편집 + 자막 완전 자동화"
```

### 4단계: 설명 생성

**설명 구조 (순서 고정):**

```
[후크 — 3~5줄]
공감을 끌어내는 문제 제기.
영상이 이 문제를 어떻게 해결하는지 한 줄 요약.

[내용 요약 — 2~3줄]
이 영상에서 보여주는 것.
다운로드/사용 방법 한 줄.

[핵심 링크]
📦 다운로드: <github_url>

[빈 줄]

📌 다루는 내용
• 항목 1
• 항목 2
• ...

[빈 줄]

⏱️ CHAPTERS
00:00 챕터1
MM:SS 챕터2
...

[빈 줄]

🔗 관련 링크
<링크들>
```

**챕터 추출 규칙:**
- 자막 전체를 읽고 내용 전환점(새 주제 시작)을 식별
- 챕터 수: 5~10개 (영상 길이에 비례)
- 타임스탬프는 자막 시작 시간 기준 (MM:SS 형식)
- 첫 챕터는 반드시 `00:00`으로 시작
- 제목은 짧고 명확하게 (10자 이내 권장)

**"다루는 내용" 작성 규칙:**
- 영상에서 실제로 다루는 내용만 작성 (추측 금지)
- 6~9개 항목
- 동사형으로 끝내기 ("설치 방법", "동작 원리", "결과 확인")

### 5단계: 파일 저장

```
<영상_파일_위치>/youtube_description.txt
```

영상 파일 경로를 모를 경우 현재 디렉토리에 저장. 타임라인이 여러 개면
`youtube_description_<타임라인 이름>.txt`로 분리.

파일 형식:
```
==============================
제목 옵션
==============================

[A] ...
[B] ...
[C] ...


==============================
유튜브 설명
==============================

(완성된 설명 전문)
```

## 사용 예시

| 사용자 발화 | 동작 |
|------------|------|
| "유튜브 설명 만들어줘" | 최근 프로젝트 확인 → 전체 파이프라인 실행 |
| "vibecut 프로젝트 설명 써줘" | "vibecut" 프로젝트 탐색 |
| "제목이랑 챕터만 만들어줘" | 제목 + CHAPTERS 섹션만 생성 |
| "깃허브 링크 https://... 넣어서 설명 써줘" | 링크를 직접 받아 config 없이 실행 |

## 주의사항

- **자막이 없는 프로젝트**는 실행 불가 — 먼저 vibecut-add-subtitles 스킬 실행 필요
- **챕터 타임스탬프**는 자막 기반이므로 실제 영상 편집 상태에 따라 ±몇 초 오차 가능 — 업로드 전 확인 권장
- **제목은 3가지 모두 출력** — 최종 선택은 사용자가 직접
- **설명 길이**: 유튜브 설명란은 5,000자 제한. 생성 후 길이 확인 및 안내
