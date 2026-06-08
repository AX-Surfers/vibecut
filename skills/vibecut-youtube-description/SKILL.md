---
name: vibecut-youtube-description
version: 1.0.0
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
   │
   ├─ [1] 전체 자막 추출 (타임스탬프 + 텍스트)
   │        ↓ 영상 흐름 파악
   │
   ├─ [2] 영상 정보 확인 (GitHub 링크, 커뮤니티 링크 등)
   │        ↓ config.json 있으면 자동 로딩, 없으면 질문
   │
   ├─ [3] 제목 3가지 생성 (클릭률 + 키워드 최적화)
   │
   ├─ [4] 설명 생성
   │        ↓ 후크 → 내용 요약 → 링크 → 📌 다루는 내용 → ⏱️ CHAPTERS
   │
   └─ [5] youtube_description.txt 저장
              ↓ 영상 파일 위치 또는 프로젝트 루트에 저장
```

## 실행 절차

### 1단계: CapCut 프로젝트 자막 전체 추출

현재 활성 CapCut 프로젝트의 `draft_info.json`에서 전체 자막을 추출한다.

```python
import json

draft_path = "/Users/seungryk/Movies/CapCut/User Data/Projects/com.lveditor.draft/<프로젝트명>/draft_info.json"

with open(draft_path, encoding="utf-8") as f:
    draft = json.load(f)

texts = draft["materials"]["texts"]
mat_map = {t["id"]: t for t in texts}
segs = draft["tracks"][1]["segments"]

subtitles = []
for seg in segs:
    start_us  = seg["target_timerange"]["start"]
    dur_us    = seg["target_timerange"]["duration"]
    start_s   = start_us / 1_000_000
    end_s     = (start_us + dur_us) / 1_000_000
    mat       = mat_map.get(seg["material_id"])
    if not mat:
        continue
    content   = json.loads(mat["content"])
    text      = content.get("text", "").strip()
    if text:
        subtitles.append({"start": start_s, "end": end_s, "text": text})

# 전체 자막 출력
for s in subtitles:
    m, sec = divmod(int(s["start"]), 60)
    print(f"  {m:02d}:{sec:02d}  {s['text']}")
```

**프로젝트 경로를 모를 경우**: 가장 최근에 수정된 프로젝트를 찾는다.
```bash
find "/Users/seungryk/Movies/CapCut/User Data/Projects/com.lveditor.draft" \
  -name "draft_info.json" -not -path "*/bak/*" \
  | xargs ls -t | head -3
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

영상 파일 경로를 모를 경우 현재 디렉토리에 저장.

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
| "유튜브 설명 만들어줘" | 현재 CapCut 프로젝트 자동 탐색 → 전체 파이프라인 실행 |
| "vibecut 프로젝트 설명 써줘" | "vibecut" 프로젝트 draft_info.json 탐색 |
| "제목이랑 챕터만 만들어줘" | 제목 + CHAPTERS 섹션만 생성 |
| "깃허브 링크 https://... 넣어서 설명 써줘" | 링크를 직접 받아 config 없이 실행 |

## 주의사항

- **자막이 없는 프로젝트**는 실행 불가 — 먼저 vibecut-add-subtitles 스킬 실행 필요
- **챕터 타임스탬프**는 자막 기반이므로 실제 영상 편집 상태에 따라 ±몇 초 오차 가능 — 업로드 전 확인 권장
- **제목은 3가지 모두 출력** — 최종 선택은 사용자가 직접
- **설명 길이**: 유튜브 설명란은 5,000자 제한. 생성 후 길이 확인 및 안내
