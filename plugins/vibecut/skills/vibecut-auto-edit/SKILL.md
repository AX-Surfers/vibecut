---
name: vibecut-auto-edit
version: 0.3.0
description: |
  Whisper 전사 기반으로 무음·NG 구간을 함께 감지·제거해 CapCut 프로젝트에 적용합니다.
  Whisper 문장 세그먼트를 클립 단위로 사용하므로 무음은 자동 제거, 문장 중간 잘림 없음.
  NG 감지(키워드/반복구절/급정지)도 동일한 전사 결과에서 추출합니다.
  (자막 추가는 vibecut-add-subtitles 스킬을 사용)
  트리거: "무음 제거", "컷편집", "캡컷 편집", "NG 제거", "/vibecut-auto-edit"
metadata:
  category: video
  locale: ko-KR
allowed-tools:
  - Bash
  - Read
  - Write
  - Edit
  - AskUserQuestion
---

# vibecut-auto-edit 스킬

**영상 → Whisper 전사 → NG 감지 → 클립 구간 생성 → CapCut 적용** 의 파이프라인.

자막 추가가 필요하면 **`vibecut-add-subtitles`** 스킬을 사용하세요.

## 핵심 원리

Whisper가 전사한 **문장 세그먼트 = 남길 구간**입니다.

- 문장 사이의 침묵·무음은 세그먼트에 포함되지 않으므로 **자동 제거**됩니다
- 문장 경계가 클립 경계이므로 **문장 중간 잘림이 원천 차단**됩니다
- 동일한 전사 결과로 NG 패턴도 함께 감지하므로 **전사를 한 번만 실행**합니다

ffmpeg silencedetect는 더 이상 사용하지 않습니다.

## 핵심 처리 흐름

```
영상 (.mov/.mp4)
   │
   ├─ [1] Whisper 전사 (detect_ng.py 내장)
   │        ↓ {stem}_words.json  ← 무음 제거 + 클립 경계 동시 해결
   │
   ├─ [2] NG 자동 감지 (detect_ng.py)
   │        ├─ 패턴 A: NG 키워드 ("잠깐", "다시", "아니" 등)
   │        ├─ 패턴 B: 반복 구절 (Jaccard ≥ 0.45)
   │        └─ 패턴 C: 짧은 발화 + 긴 침묵 급정지
   │        ↓ ng_log.json
   │
   ├─ [3] Whisper 세그먼트 기반 클립 구간 생성 (make_segments.py --words-json)
   │        ↓ final_segments.json
   │
   └─ [4] CapCut JSON 적용 (capcut_editor.py)
              ↓ 4개 파일 동시 갱신 + .locked 삭제
```

## 전제 조건

**CapCut이 실행 중이면 반드시 먼저 종료해야 합니다.** 실행 중에 draft_info.json을 수정해도 CapCut이 덮어씁니다.

```bash
# CapCut 실행 여부 확인 후 강제 종료
if pgrep -i "CapCut" > /dev/null 2>&1; then
  echo "⚠ CapCut 실행 중 — 강제 종료합니다..."
  pkill -i "CapCut"
  sleep 2
  echo "✅ CapCut 종료 완료"
else
  echo "✅ CapCut 종료 상태 확인"
fi

# uv (Python 의존성 자동 관리)
which uv || curl -LsSf https://astral.sh/uv/install.sh | sh
```

## 실행 흐름

### 단계 0: Whisper 모델 선택

**`{stem}_words.json` 캐시가 없을 때만** 사용자에게 모델을 묻습니다. 캐시가 있으면 전사를 생략하므로 이 단계를 건너뜁니다.

```python
AskUserQuestion(questions=[{
    "question": "Whisper 모델을 선택해주세요. NG 감지는 STT 정확도에 직접 영향을 받습니다.",
    "header": "Whisper 모델",
    "multiSelect": False,
    "options": [
        {"label": "small (Recommended)",
         "description": "5분 영상 기준 ~2분. 한국어 정확도가 높아 NG 키워드·반복 구절 감지에 권장."},
        {"label": "tiny",
         "description": "~30초. 가장 빠르지만 한국어 오인식 가능. NG 감지 정확도가 낮을 수 있음."},
        {"label": "base",
         "description": "~1분. tiny보다 정확하고 small보다 빠름."},
        {"label": "medium",
         "description": "~10분. 매우 정확. 중요한 영상이나 발화가 불명확한 경우."},
        {"label": "large-v3",
         "description": "~30분+. 최고 정확도. 긴 영상·전문 용어가 많은 경우."},
    ]
}])
```

### 단계 1: Whisper 전사 + NG 감지

전사와 NG 감지를 한 번에 실행합니다. `{stem}_words.json`이 이미 있으면 전사를 생략하고 NG 감지만 수행합니다.

```bash
# scripts 경로 결정: config 우선, 없으면 플러그인 캐시 자동 탐색
VIBECUT_CONFIG="${HOME}/.vibecut/config.json"
SCRIPTS=""
if [ -f "${VIBECUT_CONFIG}" ]; then
  SCRIPTS=$(python3 -c "import json; print(json.load(open('${VIBECUT_CONFIG}')).get('scripts_dir',''))" 2>/dev/null)
fi
if [ -z "${SCRIPTS}" ]; then
  SCRIPTS=$(find "${HOME}/.claude/plugins/cache/vibecut" -name "capcut_editor.py" -maxdepth 8 2>/dev/null | head -1 | xargs dirname 2>/dev/null)
fi
if [ -z "${SCRIPTS}" ]; then
  echo "❌ vibecut 스크립트를 찾을 수 없습니다. '/vibecut-setup'을 먼저 실행해주세요."
  exit 1
fi
VIDEO="<영상 파일 경로>"

uv run "${SCRIPTS}/detect_ng.py" "${VIDEO}" \
  --model "${WHISPER_MODEL}" \
  --out /tmp/ng_log.json
```

**감지 패턴 3가지:**

| 패턴 | 방식 | 예시 |
|------|------|------|
| A: 키워드 NG | 세그먼트 텍스트에 NG 키워드 포함 시 해당 구간 제거 | "잠깐", "다시", "아니", "죄송", "NG", "컷" 등 |
| B: 반복 구절 | 인접 세그먼트 Jaccard ≥ 0.45 → 앞 세그먼트 NG | "안녕하세요 저는" → "안녕하세요 저는 오늘" |
| C: 급정지 | 발화 < 3초 + 단어 < 4개 + 이후 침묵 > 1.5초 | 말하다 갑자기 멈추고 재시작 |

### 단계 2: 클립 구간 생성

Whisper 문장 세그먼트를 클립 단위로 사용해 최종 구간을 생성합니다.

```bash
WORDS_JSON="${VIDEO%.*}_words.json"

uv run "${SCRIPTS}/make_segments.py" \
  --words-json "${WORDS_JSON}" \
  --ng /tmp/ng_log.json \
  --out /tmp/final_segments.json
```

`final_segments.json` 형식: `[[start_sec, end_sec], ...]`

- 문장 사이 침묵은 세그먼트에 없으므로 자동 제거
- NG 구간은 세그먼트의 50% 이상 겹치면 제거

### 단계 3: CapCut JSON 적용

```bash
PROJECT="<CapCut 프로젝트 경로>"

uv run "${SCRIPTS}/capcut_editor.py" /tmp/final_segments.json \
  --project "${PROJECT}"
```

스크립트가 자동 처리:
1. **30fps 프레임 정렬** — 모든 타임스탬프를 프레임 경계에 정렬
2. **세그먼트마다 고유 materials 7종 생성**
3. **4개 JSON 파일 동시 저장** — 루트/bak + Timelines/UUID/ 루트/bak
4. **`.locked` 파일 자동 삭제**

## 사용자 호출 예시

| 사용자 발화 | 동작 |
|------------|------|
| "컷편집해줘" | 모델 선택 → Whisper 전사 → NG 감지 → 전체 파이프라인 |
| "NG도 같이 제거해줘" | 동일 (기본 포함) |
| "NG 감지만 다시 해줘" | `detect_ng.py`만 재실행 (words.json 캐시 재사용, 모델 질문 생략) |
| "구간 생성만 다시 해줘" | `make_segments.py`만 재실행 |

## 캐시 활용

| 파일 존재 | 동작 |
|----------|------|
| `{stem}_words.json` | Whisper 전사 생략 → NG 패턴 분석만 실행 |
| `/tmp/ng_log.json` | NG 감지 생략 → `make_segments.py` → `capcut_editor.py`만 실행 |
| `/tmp/final_segments.json` | 구간 생성 생략 → `capcut_editor.py`만 실행 |

## 자막도 함께 추가하려면

이 스킬은 **무음 + NG 제거**만 담당합니다. 자막을 추가하려면:

1. 먼저 이 스킬로 컷편집
2. 그 다음 `vibecut-add-subtitles` 스킬 호출
3. → `{stem}_words.json`이 있으면 전사 생략, `final_segments.json`이 있으면 편집 타임라인 기준으로 자막 자동 생성

## 주의사항

- **CapCut 종료 필수** — 실행 중에 파일을 수정해도 CapCut이 재실행 시 덮어씀
- **NG 키워드 오탐** — "다시"가 정상 발화에 포함될 수 있음. 결과 확인 후 `--jaccard` 조정
- **모델 선택 기준** — NG 감지는 STT 텍스트 기반이므로 small 이상 권장. tiny는 빠르지만 한국어 오인식으로 패턴 A·B 감지율이 낮을 수 있음
- **긴 영상** — 30분 영상 기준: tiny ~1분, small ~3분, medium ~15분 소요
