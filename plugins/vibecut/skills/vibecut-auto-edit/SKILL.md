---
name: vibecut-auto-edit
version: 0.5.0
description: |
  Whisper 전사 → Claude가 transcript 직접 분석 → NG 구간 제거 → CapCut 적용.
  Jaccard/키워드 방식 대신 Claude가 텍스트를 읽고 반복·실수·불완전 발화를 직접 판단.
  (자막 추가는 vibecut-add-subtitles 스킬을 사용)
  트리거: "무음 제거", "컷편집", "캡컷 편집", "NG 제거", "/vibecut-auto-edit"
metadata:
  category: video
  locale: ko-KR
allowed-tools:
  - Bash
  - Read
  - Write
  - AskUserQuestion
---

# vibecut-auto-edit 스킬

**영상 → Whisper 전사 → Claude transcript 분석 → NG 제거 → CapCut 적용** 파이프라인.

## 핵심 원리

Whisper가 전사한 단어 타임스탬프를 **Claude가 직접 읽고** NG 구간을 판단합니다.

- **기존 방식의 한계**: Jaccard는 인접 세그먼트 간 유사도만 비교 → 문장 *내부* 반복, 3~4회에 걸친 점진적 반복, 불완전 발화를 못 잡음
- **새 방식**: Claude가 전체 텍스트 흐름을 읽고 맥락 기반으로 판단 → 놓치는 NG 없음

## 핵심 처리 흐름

```
영상 (.mov/.mp4)
   │
   ├─ [0] Whisper 모델 선택 (캐시 없을 때만)
   │
   ├─ [1] Whisper 전사
   │        ↓ {stem}_words.json (단어 타임스탬프 포함)
   │
   ├─ [2] Transcript 생성 + Claude NG 분석
   │        words.json → [시간] 텍스트 형식으로 변환
   │        Claude가 직접 읽고 NG 구간 특정
   │        ↓ /tmp/ng_log.json
   │
   ├─ [3] 클립 구간 생성 (make_segments.py)
   │        ↓ /tmp/final_segments.json
   │
   └─ [4] CapCut JSON 적용 (capcut_editor.py)
              ↓ 4개 파일 동시 갱신 + .locked 삭제
```

## 전제 조건

```bash
if pgrep -i "CapCut" > /dev/null 2>&1; then
  echo "⚠ CapCut 실행 중 — 강제 종료합니다..."
  pkill -i "CapCut" && sleep 2
fi
which uv || curl -LsSf https://astral.sh/uv/install.sh | sh
```

## 실행 흐름

### 단계 0: Whisper 모델 선택

`{stem}_words.json` 캐시가 **없을 때만** 묻습니다.

```python
AskUserQuestion(questions=[{
    "question": "Whisper 모델을 선택해주세요.",
    "header": "Whisper 모델",
    "multiSelect": False,
    "options": [
        {"label": "small (Recommended)",
         "description": "5분 영상 ~2분. 한국어 정확도 높음. 대부분 권장."},
        {"label": "large-v3-turbo",
         "description": "~5분. large-v3 대비 6× 빠르고 정확도 유사. 빠른 고품질이 필요할 때."},
        {"label": "large-v3",
         "description": "~30분+. 최고 범용 정확도. 느리지만 가장 정확."},
    ]
}])
```

⚠ **커뮤니티 한국어 fine-tune 모델은 기본 옵션으로 제시하지 않습니다.**
과거 실전에서 두 개의 한국어 특화 모델이 모두 문제를 일으켰습니다:
- `ghost613/faster-whisper-large-v3-turbo-korean` — 52분 영상 중 51분을 통째로
  누락 (긴 오디오에서 디코딩 실패)
- `seastar105/whisper-medium-komixv2` — 문장은 인식하지만 단어 밀도가 낮아
  `make_segments.py`의 word-split 로직과 결합하면 실제 발화까지 무음으로
  오판해 파편 클립 양산 (→ `make_segments.py` 기본값이 `word_split=False`로
  바뀌어 이 위험은 완화되었지만, 모델 자체의 낮은 인식률 문제는 여전함)

사용자가 특정 한국어 파인튜닝 모델을 명시적으로 요청하면:
1. `curl -s -o /dev/null -w "%{http_code}"  https://huggingface.co/<repo>` 로
   실제 존재하는지 먼저 확인 (모델명을 지어내거나 추측하지 말 것)
2. CTranslate2 형식이 아니면(`library_name: transformers`) 로컬 변환 필요:
   `uv run --with ctranslate2 --with "transformers[sentencepiece]" --with torch
   ct2-transformers-converter --model <repo> --output_dir <dir> --quantization int8`
3. 변환/전사 후 **words.json의 세그먼트 수와 단어 밀도를 확인** — 영상 길이
   대비 세그먼트가 지나치게 적으면(예: 50분 영상에 30개 미만) 긴 구간을
   통째로 누락했을 가능성이 높으므로 즉시 사용자에게 알리고 검증 없이
   진행하지 말 것

### 단계 1: Whisper 전사

`{stem}_words.json` 캐시가 있으면 이 단계를 **건너뜁니다**.

```bash
# scripts 경로 결정
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
WORDS_JSON="${VIDEO%.*}_words.json"

# 전사 실행 (ng 감지 결과는 무시, words.json만 사용)
uv run "${SCRIPTS}/detect_ng.py" "${VIDEO}" \
  --model "${WHISPER_MODEL}" \
  --out /tmp/_ng_unused.json
```

### 단계 2: Transcript 생성 + Claude NG 분석

#### 2-A: 읽기 쉬운 transcript 생성

Whisper 세그먼트를 침묵 기반으로 세분화합니다 — 각 항목 = 한 번의 시도(take).
이 덕분에 Claude가 세그먼트 내부 반복까지 취로 감지합니다.

```bash
python3 - <<'PYEOF'
import json

def split_seg(seg):
    """단어 간 침묵/긴 duration으로 세분화 (Vrew 방식)."""
    words = [w for w in seg.get('words', []) if 'start' in w and 'end' in w]
    if len(words) < 3:
        return [(seg['start'], seg['end'], seg['text'].strip())]

    durs = [w['end'] - w['start'] for w in words]
    sorted_d = sorted(durs)
    trimmed = sorted_d[:max(1, int(len(sorted_d) * 0.7))]
    mean_d = sum(trimmed) / len(trimmed)
    dur_thr = max(2.5, mean_d * 3.0)

    cut_after = set()
    for i in range(len(words) - 1):
        if durs[i] > dur_thr or words[i+1]['start'] - words[i]['end'] > 1.5:
            cut_after.add(i)

    if not cut_after:
        return [(seg['start'], seg['end'], seg['text'].strip())]

    groups, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        if i in cut_after:
            text = ''.join(x['word'] for x in cur).strip()
            if cur[-1]['end'] - cur[0]['start'] >= 0.3:
                groups.append((cur[0]['start'], cur[-1]['end'], text))
            cur = []
    if cur:
        text = ''.join(x['word'] for x in cur).strip()
        if cur[-1]['end'] - cur[0]['start'] >= 0.3:
            groups.append((cur[0]['start'], cur[-1]['end'], text))

    return groups if groups else [(seg['start'], seg['end'], seg['text'].strip())]

words_path = "${WORDS_JSON}"
segs = json.loads(open(words_path).read())

lines = []
sub_count = 0
for seg in segs:
    subs = split_seg(seg)
    sub_count += len(subs)
    for ss, se, text in subs:
        m, s = divmod(int(ss), 60)
        lines.append(f"[{m:02d}:{s:02d} ({ss:.1f}~{se:.1f}s)] {text}")

transcript = '\n'.join(lines)
open("/tmp/transcript.txt", "w").write(transcript)
print(f"원본 세그먼트: {len(segs)}개 → 분할 후: {sub_count}개 (Vrew 방식)")
print(transcript[:3000])
PYEOF
```

#### 2-B: Claude가 transcript 전체를 읽고 NG 판단

`/tmp/transcript.txt`를 **전체 읽은 후** 아래 기준으로 NG 구간을 판단합니다.

> 각 줄 = 한 번의 시도(take). 타임스탬프 형식: `[MM:SS (시작~끝s)]`
> word-split으로 세분화했으므로 같은 내용의 복수 시도가 별도 줄로 보입니다.

**NG 판단 기준:**

| 패턴 | 예시 | 판단 방법 |
|------|------|----------|
| 복수 시도 | 인접 줄이 같거나 비슷한 내용 | 마지막 시도만 남기고 앞의 것 모두 NG |
| 명시적 NG 신호 | "잠깐", "다시", "아니", "죄송" | 해당 줄 + 직전 줄까지 NG |
| 불완전 발화 | 문장이 중간에 끊기고 재시작 | 짧고 의미 없는 단편 줄 |
| 급정지 후 재시작 | 직전 줄이 짧고 이후 유사 내용 등장 | 직전 줄을 NG |

**NG 구간 확장 규칙:**
- NG 신호어("다시", "잠깐")가 있으면 **신호어 이전** 발화까지 포함 (신호어가 지칭하는 NG 구간)
- 복수 시도 패턴은 **마지막 시도만 남기고** 나머지 모두 NG
- 불완전 발화는 **그 세그먼트 전체**를 NG

#### 2-C: ng_log.json 작성

분석 후 아래 형식으로 저장합니다.

```python
import json

ng_spans = [
    # [시작_초, 끝_초] — words.json의 start/end 값 기준
    # 예: [17.0, 65.2],  # "최근에 엄청난 최근에..." 반복 구간
    # 예: [180.6, 197.5], # "브라우저 AI" 3번 시도 구간
]

json.dump({"ng_spans": ng_spans}, open("/tmp/ng_log.json", "w"),
          ensure_ascii=False, indent=2)
print(f"NG 구간: {len(ng_spans)}개, 총 {sum(e-s for s,e in ng_spans):.1f}초")
```

분석 결과를 요약해서 보고합니다:
```
NG 분석 완료:
  - 문장 내 반복: N개
  - 복수 시도:   N개
  - 명시적 신호: N개
  - 불완전 발화: N개
  총 NG: N개 구간 / XX초
```

### 단계 3: 클립 구간 생성

```bash
uv run "${SCRIPTS}/make_segments.py" \
  --words-json "${WORDS_JSON}" \
  --ng /tmp/ng_log.json \
  --out /tmp/final_segments.json
```

기본값은 Whisper 세그먼트(문장) 전체를 클립 경계로 사용하며, 세그먼트 내부를
단어 단위로 더 잘게 쪼개지 않는다 (`word_split=False`가 기본). 문장이 중간에
끊기지 않아야 한다는 원칙을 지키기 위함이다. word-level 타임스탬프가
검증된 모델(공식 large-v3 등)에서 세그먼트 내부의 긴 무음까지 추가로
제거하고 싶다면 `--word-split`을 명시적으로 붙인다 — 단, 단어 인식 밀도가
낮으면 실제 발화까지 무음으로 오판해 0.4~1.6초짜리 파편 클립이 양산될 수
있으니(실전에서 확인된 실패 사례) 결과를 반드시 클립 길이 분포로 확인할 것.

⚠ **클립 끝단이 종결어미("-요", "-다" 등)를 잘라먹는 문제 (실전 실패 사례):**
Whisper의 단어 종료 타임스탬프는 실제 발음이 끝나기 살짝 전에 찍히는 경향이
있다. 크로스페이드 없이 하드컷으로 바로 이어붙이는 파이프라인 특성상 이
미세한 손실이 "문장이 잘린다"는 체감으로 이어진다. 이를 보정하기 위해 클립
끝 패딩을 시작 패딩(0.05초)보다 넉넉하게(`WORD_END_PAD_SEC = 0.2`초) 잡는
것이 기본값이다. 그래도 잘림이 느껴지면 `--pad-end 0.3` 등으로 더 늘릴 수
있다.

### 단계 4: CapCut JSON 적용

```bash
PROJECT="<CapCut 프로젝트 경로>"

uv run "${SCRIPTS}/capcut_editor.py" /tmp/final_segments.json \
  --project "${PROJECT}"
```

## 부분 재편집 (사용자가 CapCut에서 이미 손으로 다듬은 뒤 나머지만 다시 편집)

**트리거**: "N분까지는 내가 편집했어, 뒷부분만 다시 해줘", "일부만 다시 잘라줘",
"앞부분은 그대로 두고 뒤만 손봐줘" 등.

핵심: 사용자가 CapCut에서 직접 자른 구간은 **덮어쓰지 않고 그대로 보존**하며,
그 뒤(원본 영상 기준)만 새로 생성한 편집안으로 교체합니다.

1. **경계를 사용자 말로 어림잡지 말 것.** "1분까지"처럼 사용자가 말하는
   시점은 대개 편집본(target) 기준 대략치이며, 실제 원본(source) 기준 경계와
   다를 수 있다. 반드시 현재 draft_info.json을 직접 읽어서 경계를 찾는다:

   ```bash
   python3 -c "
   import json
   PROJECT = '<CapCut 프로젝트 경로>'
   d = json.load(open(PROJECT + '/draft_info.json'))
   segs = d['tracks'][0]['segments']
   for i, s in enumerate(segs):
       sr = s['source_timerange']
       print(i, sr['start']/1e6, (sr['start']+sr['duration'])/1e6)
   "
   ```

   사용자가 언급한 대략적 시점(target 기준) 근처의 클립을 찾아 그 클립의
   **source_timerange 끝 값**을 실제 경계로 사용한다. 문장이 끊기지 않도록
   경계는 항상 클립 끝(다음 클립 시작 직전)에 맞춘다.

2. 위 "단계 1~3"을 다시 실행해 **전체 영상 기준** 새 `final_segments.json`을
   생성한다 (words.json 캐시는 재사용 가능, NG 분석은 새로 하는 게 안전 —
   보존 구간 이후에서 새로 발견되는 NG가 있을 수 있음).

3. `splice_segments.py`로 보존 구간 + 신규 구간을 이어붙인다:

   ```bash
   uv run "${SCRIPTS}/splice_segments.py" \
     --project "${PROJECT}" \
     --keep-until <1단계에서 찾은 source 끝 시각> \
     --new-segments /tmp/final_segments.json \
     --out /tmp/final_segments_spliced.json
   ```

   `--keep-count <N>` 으로 클립 개수 기준 지정도 가능. 스크립트가 새 구간 중
   경계와 겹치는 것은 자동으로 건너뛰어 중복 재생을 막는다.

4. `capcut_editor.py`에 `/tmp/final_segments_spliced.json`을 적용한다
   (단계 4와 동일, CapCut 종료 확인 필수).

⚠ 사용자가 CapCut을 계속 열어두고 편집 중일 수 있으므로, 적용 직전 반드시
CapCut을 다시 종료 확인하고 draft_info.json을 **적용 시점에 새로 읽어서**
경계를 계산할 것 — 이전에 읽어둔 값을 재사용하면 그 사이 사용자가 추가로
편집한 내용을 덮어쓰게 된다.

## 캐시 활용

| 파일 존재 | 동작 |
|----------|------|
| `{stem}_words.json` | 전사 생략 → 모델 질문 없이 바로 분석 |
| `/tmp/ng_log.json` | NG 분석 생략 → 구간 생성부터 |
| `/tmp/final_segments.json` | CapCut 적용만 |

## 사용자 호출 예시

| 사용자 발화 | 동작 |
|------------|------|
| "컷편집해줘" | 전체 파이프라인 |
| "NG만 다시 분석해줘" | words.json 재사용 → transcript 재분석 → ng_log.json 재작성 |
| "구간 생성만 다시 해줘" | make_segments.py만 재실행 |
| "N분까지는 편집했어, 뒷부분만 다시 해줘" | "부분 재편집" 절차 (위 섹션) — draft_info.json에서 실제 경계 확인 → 전체 재분석 → splice_segments.py로 병합 |

## 자막도 함께 추가하려면

1. 이 스킬로 컷편집 완료
2. `vibecut-add-subtitles` 스킬 호출
3. `{stem}_words.json`·`final_segments.json` 캐시 자동 재사용

## 주의사항

- **CapCut 종료 필수**
- **NG 경계 조정** — Claude가 판단한 NG 구간이 너무 넓거나 좁으면 "NG 다시 분석해줘 + 피드백" 으로 재실행
- **긴 영상** — transcript 전체를 읽어야 하므로 영상이 30분 이상이면 분할 분석 권장
