---
name: vibecut-auto-edit
version: 0.7.0
description: |
  Whisper 전사 → Claude가 transcript 직접 분석 → NG 구간 제거(사용자 사전 검토 포함) →
  CapCut 적용 → 자막까지 자동 연결.
  Jaccard/키워드 방식 대신 Claude가 텍스트를 읽고 반복·실수·불완전 발화를 직접 판단.
  강의형 콘텐츠의 "강조용 의도적 반복"을 실수로 오판해 과잉 삭제하는 문제를 막기 위해
  컷 적용 전 번호 리스트로 NG 후보를 보여주고 승인/제외를 받는다.
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
   ├─ [0] Whisper 모델 결정 (large-v3-turbo 고정, 질문 생략)
   │
   ├─ [1] Whisper 전사
   │        ↓ {stem}_words.json (단어 타임스탬프 포함)
   │
   ├─ [2] Transcript 생성 + Claude NG 분석
   │        words.json → [시간] 텍스트 형식으로 변환
   │        Claude가 직접 읽고 NG 구간 특정
   │        ↓ /tmp/ng_log.json
   │
   ├─ [2-D] 사용자 사전 검토 ★신규
   │        NG 후보를 번호 리스트로 제시 → 자유 텍스트로 승인/제외
   │        ↓ /tmp/ng_log.json (필터링 반영)
   │
   ├─ [3] 클립 구간 생성 (make_segments.py)
   │        ↓ /tmp/final_segments.json
   │
   ├─ [4] CapCut JSON 적용 (capcut_editor.py)
   │        ↓ 4개 파일 동시 갱신 + .locked 삭제
   │
   └─ [5] 자막 자동 연결 ★신규
            vibecut-add-subtitles(모드 B)를 이어서 호출, words.json 캐시 재사용
```

## 전제 조건

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

uv run "${SCRIPTS}/_platform.py" quit-capcut
```

## 실행 흐름

### 단계 0: Whisper 모델 결정

기본값을 **`large-v3-turbo`로 고정**하고 질문 없이 바로 진행합니다. large-v3 대비
6배 빠르면서 정확도는 거의 동일해 전문 용어가 섞인 강의형 콘텐츠에도 충분합니다.

```bash
WHISPER_MODEL="large-v3-turbo"
```

사용자가 이번 실행만 다른 모델을 명시적으로 요청하면(예: "이번엔 large로 해줘",
"small로 빠르게") 그 값을 대신 사용합니다. `{stem}_words.json` 캐시가 있으면
이 단계 자체를 건너뜁니다.

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
  APP_DIR="${HOME}/.vibecut/app"
  if [ -d "${APP_DIR}/.git" ]; then
    git -C "${APP_DIR}" pull --ff-only >/dev/null 2>&1
  else
    git clone --depth 1 https://github.com/AX-Surfers/Vibecut.git "${APP_DIR}" >/dev/null 2>&1
  fi
  [ -f "${APP_DIR}/scripts/capcut_editor.py" ] && SCRIPTS="${APP_DIR}/scripts"
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

### 단계 2-D: 사용자 사전 검토 (컷 적용 전 필수)

강의형 콘텐츠는 강조를 위해 같은 말을 의도적으로 반복하는 경우가 많아, "복수 시도"
판정이 실제 실수가 아닌 의도적 반복을 잘라내는 오탐(과잉 삭제)을 낼 수 있습니다.
**컷을 실제로 적용하기 전에** `/tmp/ng_log.json`의 각 구간을 번호 리스트로 제시하고
사용자의 자유 텍스트 승인/제외를 받습니다. 영상 전체를 재생해 확인하는 것보다
텍스트 리스트만 훑는 게 훨씬 빠르므로, 이 단계는 생략하지 않습니다.

```python
import json, re

transcript = open("/tmp/transcript.txt", encoding="utf-8").read().splitlines()
ng = json.load(open("/tmp/ng_log.json", encoding="utf-8"))
spans = ng["ng_spans"]

line_re = re.compile(r"\((\d+\.?\d*)~(\d+\.?\d*)s\)\]\s*(.*)")
parsed = []
for line in transcript:
    m = line_re.search(line)
    if m:
        parsed.append((float(m.group(1)), float(m.group(2)), m.group(3)))

def context_for(span):
    s, e = span
    hit = [text for ls, le, text in parsed if ls < e and le > s]
    return " / ".join(hit)[:80] or "(텍스트 없음)"

for i, span in enumerate(spans, 1):
    s, e = span
    m, sec = divmod(int(s), 60)
    print(f"[{i}] {m:02d}:{sec:02d} ({e-s:.1f}초) — \"{context_for(span)}\"")
```

리스트를 출력한 뒤 사용자에게 묻습니다:

```
위 N개 구간을 NG로 판단했습니다. 전체 적용해도 될까요?
(예: "전체 적용" / "2, 7번 빼고 적용" / "1번만 남기고 나머지 빼줘")
```

**사용자의 자유 텍스트 답변을 받아** 제외 지시된 번호를 `spans`에서 제거한 뒤
`/tmp/ng_log.json`을 다시 씁니다:

```python
# excluded = 사용자가 제외를 요청한 번호 목록 (1-based), 예: [2, 7]
kept = [span for i, span in enumerate(spans, 1) if i not in excluded]
json.dump({"ng_spans": kept}, open("/tmp/ng_log.json", "w"),
          ensure_ascii=False, indent=2)
print(f"검토 반영: {len(spans)}개 → {len(kept)}개 적용")
```

사용자가 "전체 적용"이라고 답하면 필터링 없이 그대로 진행합니다.

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
끝 패딩을 시작 패딩보다 넉넉하게(`WORD_END_PAD_SEC = 0.2`초) 잡는 것이
기본값이다. 그래도 잘림이 느껴지면 `--pad-end 0.3` 등으로 더 늘릴 수 있다.

⚠ **클립 시작단이 앞글자를 잘라먹는 문제 (실전 실패 사례):**
Whisper의 단어 시작 타임스탬프도 종료 타임스탬프와 마찬가지로 실제 발음
시작보다 살짝 늦게 찍히는 경향이 있어, 자음이나 앞음절이 잘려나간다
(예: "지도만"의 "지"가 잘려 "도만"으로 들림). 기존 시작 패딩 0.05초는
이를 방지하기에 부족했으므로 `WORD_START_PAD_SEC = 0.12`초로 상향했다.
그래도 앞글자가 잘리면 `--pad-start 0.18` 등으로 더 늘릴 수 있다 — 단,
너무 크게 잡으면 직전 클립의 꼬리(이미 pad_end로 보정됨)와 겹쳐 말이
중복 재생될 수 있으니 끝단 패딩보다는 작게 유지할 것.

### 단계 3-B: 적용 전 프로젝트 상태 점검 (필수)

컷을 적용하면 대상 트랙의 세그먼트가 **통째로 교체**된다. 잘못된 트랙을 잡으면
되돌리기 어려우므로, 적용 직전에 현재 프로젝트 구성을 눈으로 확인한다.

```bash
PROJECT="<CapCut 프로젝트 경로>"
python3 - <<PYEOF
import json
from collections import Counter
d = json.load(open("${PROJECT}/draft_info.json"))
mats = {m['id']: m.get('path','') for m in d['materials'].get('videos', [])}
print(f"전체 길이: {d['duration']/1e6:.1f}초")
for i, t in enumerate(d['tracks']):
    names = Counter(mats.get(s.get('material_id'),'?').split('/')[-1]
                    for s in t.get('segments', []))
    print(f"  tracks[{i}] {t['type']}: {len(t.get('segments',[]))}개 {dict(names)}")
PYEOF
```

확인 항목:
- **비디오 트랙이 2개 이상**이면 어느 쪽이 원본 영상인지 확인한다. 배경 이미지나
  오버레이가 올라가 있으면 `--track N`으로 대상을 명시하거나, 먼저 그 트랙을
  타임라인에서 빼고 진행한다.
- **세그먼트 수·전체 길이가 예상과 다르면** 그 사이 사용자가 CapCut에서 편집했거나
  이전 작업이 꼬인 것이다. 덮어쓰기 전에 사용자에게 확인한다.

### 단계 4: CapCut JSON 적용

```bash
PROJECT="<CapCut 프로젝트 경로>"

uv run "${SCRIPTS}/capcut_editor.py" /tmp/final_segments.json \
  --project "${PROJECT}"
```

⚠ **엉뚱한 트랙을 편집하는 사고 (실전 실패 사례):**
`capcut_editor.py`는 예전에 `tracks[0]`을 무조건 편집 대상으로 삼았다. 사용자가
배경 이미지를 타임라인에 올려두면 그 이미지 트랙이 `tracks[0]`이 되어, 원본 영상
대신 **PNG 한 장이 38조각으로 잘리고** 정작 영상 트랙은 그대로 남는 사고가 났다.
지금은 실제 영상 파일을 참조하는 트랙을 자동 선택하고, 대상이 이미지면 중단한다.
그래도 다음을 지킬 것:
- 실행 로그의 `🎬 영상 1: <파일명>` 줄에서 **원본 영상 파일명이 맞는지 반드시 확인**한다.
  파일명이 다르거나 `원본 NNN분`이 비상식적으로 크면 즉시 중단하고 되돌린다.
- `⚠ 비디오 트랙이 N개입니다` 경고가 뜨면 선택된 트랙이 맞는지 확인한다.
- 트랙을 강제하려면 `--track N`을 쓴다.

⚠ **되돌리기:** 적용 전 상태는 `<프로젝트상위>/.vibecut_backups/<프로젝트명>/`에
타임스탬프 tar.gz로 자동 백업된다. 사고 시 아래로 복원한다.

```bash
tar -xzf "<...>/.vibecut_backups/<프로젝트>/<타임스탬프>_capcut_editor.tar.gz" \
  -C "${PROJECT}"
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
| "컷편집해줘" | 전체 파이프라인 (NG 분석 → 사전 검토 승인 → 컷 적용 → 자막까지 자동 연결) |
| "NG만 다시 분석해줘" | words.json 재사용 → transcript 재분석 → ng_log.json 재작성 → 사전 검토부터 다시 |
| "구간 생성만 다시 해줘" | make_segments.py만 재실행 |
| "컷편집만 해줘 (자막은 나중에)" | 단계 5(자막 자동 연결) 생략, 컷 적용까지만 |
| "N분까지는 편집했어, 뒷부분만 다시 해줘" | "부분 재편집" 절차 (위 섹션) — draft_info.json에서 실제 경계 확인 → 전체 재분석 → splice_segments.py로 병합 |

## 단계 5: 자막 자동 연결

컷 적용(단계 4)이 끝나면, 사용자가 "자막은 나중에"/"컷편집만" 이라고 명시하지
않은 한 **바로 이어서** `vibecut-add-subtitles` 스킬을 **모드 B**(이미 편집된
CapCut 프로젝트에 자막 추가)로 호출합니다. `{stem}_words.json`은 이미 있지만
모드 B는 컷 적용 후의 편집 타임라인 기준으로 별도 전사가 필요하므로
`{PROJECT}_edited_words.json` 캐시가 없으면 그 스킬 안에서 새로 전사합니다
(Whisper 모델은 동일하게 `large-v3-turbo` 고정, 질문 생략).

## 주의사항

- **CapCut 종료 필수**
- **NG 리스트 검토가 1차 방어선** — 사전 검토에서 "N번은 왜 뺐어?" 처럼 개별
  구간에 대한 피드백을 주면, 다음 실행부터 유사 패턴 판단에 반영해 재분석
- **긴 영상** — transcript 전체를 읽어야 하므로 영상이 30분 이상이면 분할 분석 권장
- **적용 로그를 읽고 넘어갈 것** — `🎬 영상 1: <파일명>`과 감소율이 예상과 맞는지
  확인한다. 이 한 줄만 봤어도 이미지를 자르는 사고를 즉시 잡을 수 있었다.

### ⚠ CapCut GUI를 직접 조작하지 말 것 (실전 실패 사례)

이 스킬은 CapCut을 **종료한 상태에서 JSON을 직접 고치는** 방식이다. 화면 자동화로
CapCut UI를 조작하는 것은 이 파이프라인과 섞이면 위험하다:

- CapCut의 드래그앤드롭·컬러 피커는 좌표 기반 자동화와 잘 맞지 않아 실패가 잦고,
  실패를 되돌리려 누른 `Cmd+Z`가 **사용자의 기존 편집까지 되돌린다**. 실제로
  영상 42컷 → 34컷, 자막 173개 → 130개가 소실된 사고가 있었다.
- 실패한 조작이 타임라인에 잔재(예: 배경 이미지 1클립)를 남기면, 이후 컷 적용이
  그 잔재를 편집 대상으로 잡는 2차 사고로 이어진다.

미디어 가져오기·크로마키·클립 배치처럼 GUI가 필요한 작업은 **사용자에게 절차를
안내**하고 맡긴다. 부득이 자동화한다면 먼저 프로젝트 폴더를 통째로 백업하고,
`Cmd+Z`를 되돌리기 수단으로 쓰지 않는다(무엇이 되돌려질지 알 수 없다).

### ⚠ 자막 싱크: ffconcat inpoint/outpoint는 부정확하다 (실전 실패 사례)

단계 5(자막 연결)에서 편집본 오디오를 만들 때 `ffmpeg -f concat` +
`inpoint/outpoint`를 쓰면 구간당 약 0.4초씩 초과 추출되어, 38컷 기준 **16.8초**가
밀렸다(465.8초 타임라인 → 482.5초 오디오). 자막이 뒤로 갈수록 어긋난다.

각 구간을 `-ss/-t`로 개별 추출해 이어붙이면 정확하다:

```python
import json, subprocess, os, pathlib
PROJECT = "<CapCut 프로젝트 경로>"
data = json.loads(pathlib.Path(PROJECT, "draft_info.json").read_text())
mats = {v["id"]: v["path"] for v in data["materials"]["videos"]}
segs = next(sorted(t["segments"], key=lambda s: s["target_timerange"]["start"])
            for t in data["tracks"] if t["type"] == "video")

with open("/tmp/edited.raw", "wb") as f:
    for seg in segs:
        src = seg["source_timerange"]
        p = subprocess.run([
            "ffmpeg", "-v", "error",
            "-ss", f"{src['start']/1e6:.6f}", "-t", f"{src['duration']/1e6:.6f}",
            "-i", mats[seg["material_id"]],
            "-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", "-"
        ], capture_output=True)
        f.write(p.stdout)
```

```bash
ffmpeg -y -v error -f s16le -ar 16000 -ac 1 -i /tmp/edited.raw \
  -acodec pcm_s16le /tmp/${PROJECT_NAME}_edited.wav
```

**검증 필수**: 만든 wav 길이와 `draft_info.json`의 `duration`이 **0.1초 이내로
일치**해야 한다. 어긋나면 그대로 자막 오차가 된다.

```bash
ffprobe -v error -show_entries format=duration -of csv=p=0 /tmp/${PROJECT_NAME}_edited.wav
```

### ⚠ subtitle_splits.json 형식 — 인덱스가 아니라 텍스트 조각

`add_subtitles.py`의 `split_by_splits()`는 각 원소를 **분할된 텍스트 문자열 배열**로
기대한다. 분할 지점 인덱스를 넘기면 `AttributeError: 'int' object has no attribute
'strip'`으로 죽는다.

```json
[[], ["이번 영상에서는 프롬프트를 작성할 때", "엔트로픽 개발자가 사용하는 방법과"], []]
```

분할 에이전트에 이 형식을 명시하고, 넘기기 전에 **공백 제거 후 원문과 일치하는지**
검증한다(글자 유실·변형 방지).
