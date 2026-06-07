---
name: vibecut-auto-edit
version: 0.2.0
description: |
  영상의 무음 구간과 NG 구간을 자동으로 감지·제거해 CapCut 프로젝트에 적용합니다.
  ffmpeg silencedetect + Whisper 기반 NG 자동 감지(키워드/반복구절/급정지) 컷편집.
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

**영상 → 무음 자동 감지 → NG 자동 감지 → CapCut 컷편집 자동 적용** 의 파이프라인.

자막 추가가 필요하면 **`vibecut-add-subtitles`** 스킬을 사용하세요.

## 핵심 처리 흐름

```
영상 (.mov/.mp4)
   │
   ├─ [1] ffmpeg silencedetect (-35dB 기준)
   │        ↓ silence_data.txt
   │
   ├─ [2] 발화 구간 계산 (make_segments.py)
   │        ↓ speech_segments.json
   │
   ├─ [3] NG 자동 감지 (detect_ng.py)
   │        ├─ 패턴 A: NG 키워드 ("잠깐", "다시", "아니" 등)
   │        ├─ 패턴 B: 반복 구절 (Jaccard ≥ 0.6)
   │        └─ 패턴 C: 짧은 발화 + 긴 침묵 급정지
   │        ↓ {stem}_ng_log.json
   │
   ├─ [4] NG 필터 적용 (make_segments.py --ng)
   │        ↓ final_segments.json
   │
   └─ [5] CapCut JSON 적용 (capcut_editor.py)
              ↓ 4개 파일 동시 갱신 + .locked 삭제
```

## 전제 조건

```bash
# CapCut 완전 종료 (자동 종료됨)
ps aux | grep "CapCut.app/Contents/MacOS/CapCut" | grep -v grep

# ffmpeg
which ffmpeg

# uv
which uv || curl -LsSf https://astral.sh/uv/install.sh | sh
```

## 실행 흐름

### 단계 1: 무음 구간 감지

```bash
ffmpeg -i "${VIDEO}" -af silencedetect=noise=-30dB:d=0.5 -f null - 2>&1 \
  | grep -E "silence_(start|end)" > /tmp/silence_data.txt
```

**기본 파라미터:**
- 무음 임계값: **-30dB** (이보다 작은 소리는 무음으로 간주 — 호흡음·룸톤 포함)
- 최소 무음 길이: **0.5초** (이보다 짧으면 무음 아님)

### 단계 2: 발화 구간 계산

silence_data.txt를 파싱해 speech_segments.json 생성:

```python
# Python으로 silence → speech 변환
python3 << 'EOF'
import re, json
starts, ends = [], []
with open("/tmp/silence_data.txt") as f:
    for line in f:
        m = re.search(r'silence_start: ([\d.]+)', line)
        if m: starts.append(float(m.group(1)))
        m = re.search(r'silence_end: ([\d.]+)', line)
        if m: ends.append(float(m.group(1)))

DURATION = float(open("/tmp/video_duration.txt").read().strip())
PAD, MIN_DUR = 0.05, 0.3
speech = []
if starts and starts[0] > PAD:
    speech.append([0.0, starts[0]])
for i in range(len(ends)):
    s = ends[i] + PAD
    e = (starts[i+1] - PAD) if i+1 < len(starts) else DURATION
    if e - s >= MIN_DUR:
        speech.append([round(s, 3), round(e, 3)])
json.dump(speech, open("/tmp/speech_segments.json", "w"))
print(f"발화 구간: {len(speech)}개")
EOF
```

**사용자 편집 패턴을 반영한 기본값:**
- 갭 병합 간격: **0.5초** (이보다 짧은 휴지는 합침)
- 최소 발화 길이: **0.3초**
- PAD: 0.05초 (앞뒤 여유)

### 단계 3: NG 자동 감지 (항상 실행)

```bash
SCRIPTS="/Users/seungryk/youtube/vibecut/scripts"

uv run "${SCRIPTS}/detect_ng.py" "${VIDEO}" \
  --speech /tmp/speech_segments.json \
  --out /tmp/ng_log.json
```

**감지 패턴 3가지:**

| 패턴 | 방식 | 예시 |
|------|------|------|
| A: 키워드 NG | 세그먼트 텍스트에 NG 키워드 포함 시 해당 구간 제거 | "잠깐", "다시", "아니", "죄송", "NG", "컷" 등 |
| B: 반복 구절 | 인접 세그먼트 Jaccard ≥ 0.6 → 앞 세그먼트 NG | "안녕하세요 저는" → "안녕하세요 저는 오늘" |
| C: 급정지 | 발화 < 2초 + 단어 < 3개 + 이후 침묵 > 1.5초 | 말하다 갑자기 멈추고 재시작 |

**Whisper 재사용:**
- `{stem}_words.json`이 이미 있으면 (add-subtitles 선행 시) 전사 생략
- 없으면 `tiny` 모델로 자동 전사 (약 실시간 10배 속도)

### 단계 4: NG 필터 적용 + 최종 구간 생성

**`{stem}_words.json`이 있으면 Whisper 세그먼트 기반 모드 (권장):**

```bash
WORDS_JSON="${VIDEO%.*}_words.json"
if [ -f "${WORDS_JSON}" ]; then
  # Whisper 문장 단위 클립 생성 → 문장 중간 잘림 방지
  uv run "${SCRIPTS}/make_segments.py" \
    --words-json "${WORDS_JSON}" \
    --ng /tmp/ng_log.json \
    --out /tmp/final_segments.json
else
  # words.json 없을 때 ffmpeg 발화 구간 기반 폴백
  uv run "${SCRIPTS}/make_segments.py" \
    --speech /tmp/speech_segments.json \
    --ng /tmp/ng_log.json \
    --out /tmp/final_segments.json
fi
```

**왜 Whisper 세그먼트 기반인가:**
- ffmpeg는 물리적 소리 경계(호흡 포함)로 구간을 분리 → 문장 중간이 잘릴 수 있음
- Whisper는 의미 단위(문장)로 세그먼트를 분리 → 문장 중간 잘림 원천 차단
- 문장 사이 침묵은 취할 구간만 열거하므로 자동 제거

**NG 제거 임계값: 50%** — 세그먼트의 절반 이상이 NG로 마킹된 경우만 제거.

### 단계 5: CapCut JSON 적용

```bash
uv run "${SCRIPTS}/capcut_editor.py" /tmp/final_segments.json \
  --project "<CapCut 프로젝트 경로>"
```

스크립트가 자동 처리:
1. **30fps 프레임 정렬** — 모든 타임스탬프를 프레임 경계에 정렬
2. **세그먼트마다 고유 materials 7종 생성**
3. **4개 JSON 파일 동시 저장** — 루트/bak + Timelines/UUID/ 루트/bak
4. **`.locked` 파일 자동 삭제**

## 사용자 호출 예시

| 사용자 발화 | 동작 |
|------------|------|
| "컷편집해줘" | 무음 감지 → NG 자동 감지 → 전체 파이프라인 |
| "NG도 같이 제거해줘" | 동일 (기본 포함) |
| "무음 0.3초 이상 다 잘라" | `silencedetect=...:d=0.3`으로 조정 |
| "임계값 -40dB로" | `silencedetect=noise=-40dB:d=0.5`로 변경 |
| "NG 감지만 다시 해줘" | detect_ng.py만 재실행 (words.json 캐시 재사용) |

## 캐시 활용

| 파일 존재 | 동작 |
|----------|------|
| `{stem}_words.json` | Whisper 전사 생략, NG 패턴 분석만 |
| `/tmp/speech_segments.json` | 무음 감지 생략, NG 감지부터 |
| `/tmp/ng_log.json` | NG 감지 생략, make_segments → capcut_editor만 |

## 자막도 함께 추가하려면

이 스킬은 **무음 + NG 제거**만 담당합니다. 자막을 추가하려면:

1. 먼저 이 스킬로 컷편집
2. 그 다음 `vibecut-add-subtitles` 스킬 호출
3. → `final_segments.json`이 있으면 편집 타임라인 기준으로 자막 자동 생성

## 주의사항

- **CapCut 종료 필수** — 실행 중에는 변경사항 무시
- **임계값 조정** — 영상에 BGM이 있거나 잡음이 많으면 -35dB로는 부족. -25dB ~ -30dB 시도
- **NG 키워드 오탐** — "다시"가 정상 발화에 포함될 수 있음. 결과 확인 후 `--jaccard` 조정
- **tiny 모델 한계** — NG 키워드 오인식 가능. `--model small` 사용 시 정확도 향상
