---
name: vibecut-auto-edit
version: 0.1.0
description: |
  영상의 무음 구간을 자동으로 감지·제거해 CapCut 프로젝트에 적용합니다.
  ffmpeg silencedetect 기반 컷편집. (자막 추가는 vibecut-add-subtitles 스킬을 사용)
  트리거: "무음 제거", "컷편집", "캡컷 편집", "/vibecut-auto-edit"
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

**영상 → 무음 자동 감지 → CapCut 컷편집 자동 적용** 의 파이프라인.

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
   ├─ [3] (선택) NG 구간 추가 필터링
   │        ↓ final_segments.json
   │
   └─ [4] CapCut JSON 적용 (capcut_editor.py)
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
ffmpeg -i "${VIDEO}" -af silencedetect=noise=-35dB:d=0.5 -f null - 2>&1 \
  | grep -E "silence_(start|end)" > /tmp/silence_data.txt
```

**기본 파라미터:**
- 무음 임계값: **-35dB** (이보다 작은 소리는 무음으로 간주)
- 최소 무음 길이: **0.5초** (이보다 짧으면 무음 아님)

### 단계 2: 발화 구간 계산

```bash
SCRIPTS="${CLAUDE_PLUGIN_ROOT}/scripts"
uv run "${SCRIPTS}/make_segments.py" \
  --silence /tmp/silence_data.txt \
  --duration $(ffprobe -v quiet -show_entries format=duration -of csv=p=0 "${VIDEO}") \
  --out /tmp/speech_segments.json
```

**사용자 편집 패턴을 반영한 기본값:**
- 갭 병합 간격: **0.5초** (이보다 짧은 휴지는 합침)
- 최소 발화 길이: **0.3초**
- PAD: 0.05초 (앞뒤 여유)

### 단계 3 (선택): NG 구간 필터링

NG 로그 파일이 있다면 (`*_ng_log.json`):

```bash
uv run "${SCRIPTS}/make_segments.py" \
  --speech /tmp/speech_segments.json \
  --ng "<영상명>_ng_log.json" \
  --out /tmp/final_segments.json
```

**NG 제거 임계값: 50%** — 발화 구간의 절반 이상이 NG로 마킹된 경우만 제거.

### 단계 4: CapCut JSON 적용

```bash
uv run "${SCRIPTS}/capcut_editor.py" /tmp/final_segments.json
```

스크립트가 자동 처리:
1. **30fps 프레임 정렬** — 모든 타임스탬프를 프레임 경계에 정렬
2. **세그먼트마다 고유 materials 7종 생성** — videos, speeds, placeholder_infos, canvases, sound_channel_mappings, material_colors, vocal_separations
3. **4개 JSON 파일 동시 저장** — 루트/bak + Timelines/UUID/ 루트/bak
4. **`.locked` 파일 자동 삭제**

## 사용자 호출 예시

| 사용자 발화 | 동작 |
|------------|------|
| "무음 제거해줘" | 영상 선택 → -35dB / 0.5초 기본값으로 처리 |
| "무음 0.3초 이상 다 잘라" | `silencedetect=...:d=0.3`으로 조정 |
| "NG 구간도 같이 제거" | NG 로그 파일 자동 탐색 → 통합 처리 |
| "임계값 -40dB로" | `silencedetect=noise=-40dB:d=0.5`로 변경 |

## 입력 SRT 형식

`segments.json`은 `[[start_sec, end_sec], ...]` 배열:

```json
[
  [0.5, 5.2],
  [5.8, 12.4],
  [13.0, 18.7]
]
```

이 구간들만 남기고 나머지는 잘림.

## 자막도 함께 추가하려면

이 스킬은 **무음 제거만** 담당합니다. 자막을 함께 추가하려면:

1. 먼저 이 스킬로 무음 제거
2. 그 다음 `vibecut-add-subtitles` 스킬 호출
3. → 자막은 컷편집된 영상이 아닌 **원본 영상**에 매칭됨에 주의

**또는** 자막 후 무음 제거 순서로 작업하면 자막이 자동으로 컷편집된 타임라인에 따라감.

## 주의사항

- **CapCut 종료 필수** — 실행 중에는 변경사항 무시
- **임계값 조정** — 영상에 BGM이 있거나 잡음이 많으면 -35dB로는 부족. -25dB ~ -30dB 시도
- **최소 무음 길이** — 너무 짧게 (0.2초 이하) 잡으면 자연스러운 호흡까지 잘림
