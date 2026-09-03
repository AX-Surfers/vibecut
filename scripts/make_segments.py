#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""
Vibecut — CapCut 컷편집 구간 생성 (의존성 없음)

Whisper words.json의 세그먼트(문장)를 클립 경계로 삼고, NG 구간(ng_log.json)을
잘라낸 뒤 가까운 구간을 병합해 [[start, end], ...]를 만든다.

사용법:
  uv run make_segments.py --words-json <영상>_words.json --ng <영상>_ng_log.json
  → 기본 출력: <영상>_segments.json (words.json 옆)
"""

import argparse
import json
from pathlib import Path

# ──────────────────────────────────────────────
# 파라미터
# ──────────────────────────────────────────────

# NG 점유 비율 임계값: 문장에서 NG가 이 비율 이상이면 문장을 통째로 버린다.
# 예전 키워드/Jaccard 감지는 경계가 부정확해 50%로 "봐주는" 규칙이 필요했지만,
# 지금은 Claude가 정확한 구간을 찍으므로 찍은 곳만 잘라야 한다. 0.5였을 때
# 같은 문장 안에 있던 살릴 재녹음 테이크까지 통째로 날아가는 사고가 있었다.
NG_REMOVE_THRESHOLD = 1.0

# NG 구간 앞뒤 분리 시 최소 잔여 길이 — 이보다 짧은 조각은 버림
MIN_RESIDUAL_SEC = 0.3

# 인접 구간 사이 갭이 이 이하면 병합
MERGE_GAP_SEC = 0.5

# 최소 발화 구간 길이 — 병합 후에도 이보다 짧으면 버림
MIN_SPEECH_SEC = 0.3

# 클립 끝단 패딩 (초)
# Whisper 단어 종료 타임스탬프는 실제 발음이 끝나기 살짝 전에 찍히는 경향이
# 있음 (한국어 종결어미 "-요"/"-다" 등에서 두드러짐). 크로스페이드 없는
# 하드컷 파이프라인 특성상 이 미세한 손실이 "문장이 잘린다"는 체감으로
# 이어지므로, 시작 패딩보다 넉넉하게 잡는다.
WORD_END_PAD_SEC = 0.2

# 클립 시작단 패딩 (초) — 실전 실패 사례
# 단어 시작 타임스탬프도 실제보다 살짝 늦게 찍혀 자음/앞음절이 잘린다
# (예: "지도만"의 "지"가 잘려 "도만"으로 들림). 0.05초는 부족해 0.12초로 상향.
# 끝단 패딩보다는 작게 유지 — 직전 클립 꼬리와 겹치면 말이 중복 재생된다.
WORD_START_PAD_SEC = 0.12

# 단어 수준 sub-segment 분리 (opt-in)
WORD_SPLIT_ABS_SEC = 2.5   # 이 초 이상인 단어는 무음 내포로 판단 (절대 임계값)
WORD_SPLIT_MUL    = 3.0    # 세그먼트 평균 단어 길이의 N배 이상이면 분리


# ──────────────────────────────────────────────
# 유틸
# ──────────────────────────────────────────────

def overlap(s1, e1, s2, e2):
    """두 구간의 겹치는 길이"""
    return max(0.0, min(e1, e2) - max(s1, s2))


def ng_ratio(ss, se, ng_spans):
    """speech 구간에서 NG가 차지하는 비율"""
    dur = se - ss
    if dur <= 0:
        return 0.0
    return sum(overlap(ss, se, ns, ne) for ns, ne in ng_spans) / dur


# ──────────────────────────────────────────────
# NG 처리
# ──────────────────────────────────────────────

def apply_ng_filter(speech_spans, ng_spans, threshold=NG_REMOVE_THRESHOLD,
                    min_residual=MIN_RESIDUAL_SEC):
    """
    각 speech 구간에서:
      - NG 비율 >= threshold → 구간 제거
      - 그 외 → NG 구간만 잘라내고 min_residual 이상 남은 조각만 유지
    """
    result = []
    for ss, se in speech_spans:
        ratio = ng_ratio(ss, se, ng_spans)
        if ratio >= threshold:
            continue
        if ratio == 0.0:
            result.append((ss, se))
            continue

        ng_in_range = sorted(
            [(max(ss, ns), min(se, ne)) for ns, ne in ng_spans
             if overlap(ss, se, ns, ne) > 0]
        )
        pieces = []
        cur = ss
        for ns, ne in ng_in_range:
            if cur < ns - 0.01:
                pieces.append((cur, ns))
            cur = max(cur, ne)
        if cur < se - 0.01:
            pieces.append((cur, se))

        for ps, pe in pieces:
            if pe - ps >= min_residual:
                result.append((ps, pe))
    return result


# ──────────────────────────────────────────────
# 갭 병합
# ──────────────────────────────────────────────

def merge_gaps(spans, gap_sec=MERGE_GAP_SEC, min_dur=MIN_SPEECH_SEC):
    """인접한 구간 사이 갭이 gap_sec 이하이면 병합. 병합 후 min_dur 미만은 제거."""
    if not spans:
        return []
    spans = sorted(spans)
    merged = [list(spans[0])]
    for s, e in spans[1:]:
        if s - merged[-1][1] <= gap_sec:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return [(s, e) for s, e in merged if e - s >= min_dur]


# ──────────────────────────────────────────────
# 단어 수준 sub-segment 분리 (opt-in)
# ──────────────────────────────────────────────

def split_segment_by_long_words(segment, abs_threshold=WORD_SPLIT_ABS_SEC,
                                 mul_threshold=WORD_SPLIT_MUL,
                                 gap_threshold=1.5,
                                 min_dur=MIN_SPEECH_SEC):
    """Whisper 세그먼트 내 무음 구간을 감지해 sub-segment로 분리.

      [A] 단어 duration > max(abs_threshold, mean × mul_threshold)
          → 무음이 단어 duration에 흡수된 것 (small/medium 계열)
      [B] 단어 사이 gap > gap_threshold → 실제 gap (large-v3 계열)
    """
    words = [w for w in segment.get('words', [])
             if 'start' in w and 'end' in w]
    if len(words) < 3:
        return [(segment['start'], segment['end'])]

    durations = [w['end'] - w['start'] for w in words]
    sorted_d = sorted(durations)
    trimmed = sorted_d[:max(1, int(len(sorted_d) * 0.7))]
    mean_dur = sum(trimmed) / len(trimmed)
    dur_threshold = max(abs_threshold, mean_dur * mul_threshold)

    split_points = []  # (group1_end, group2_start)
    for i, (w, dur) in enumerate(zip(words[:-1], durations[:-1])):
        if dur > dur_threshold:
            split_points.append((w['start'], w['end']))
            continue
        next_w = words[i + 1]
        if next_w['start'] - w['end'] > gap_threshold:
            split_points.append((w['end'], next_w['start']))

    if not split_points:
        return [(segment['start'], segment['end'])]

    split_points.sort()
    merged = [split_points[0]]
    for g1e, g2s in split_points[1:]:
        if g1e >= merged[-1][1]:
            merged.append((g1e, g2s))

    result = []
    cur_start = segment['start']
    for group1_end, group2_start in merged:
        if group1_end - cur_start >= min_dur:
            result.append((cur_start, group1_end))
        cur_start = group2_start
    if segment['end'] - cur_start >= min_dur:
        result.append((cur_start, segment['end']))
    return result if result else [(segment['start'], segment['end'])]


# ──────────────────────────────────────────────
# Whisper 세그먼트 기반 클립 단위 생성
# ──────────────────────────────────────────────

def build_from_words_json(words_json_path, ng_spans=None, pad=None, pad_end=None,
                          merge_gap=MERGE_GAP_SEC, min_dur=MIN_SPEECH_SEC,
                          ng_threshold=NG_REMOVE_THRESHOLD,
                          word_split=False):
    """faster-whisper words.json 세그먼트를 클립 단위로 변환.

    문장(세그먼트) 단위를 클립 경계로 쓰므로 문장 중간 잘림이 없고, 문장 사이
    침묵은 자동 제거된다. 문장 안의 정적은 Claude가 ng_log.json에 찍어 준다
    (make_transcript.py가 정적을 표시해 준다).

    ⚠ word_split=True 주의 (실전 실패 사례로 기본값 False):
      단어 인식 밀도가 낮은 모델(커뮤니티 fine-tune 등)에서는 단어 사이 간격이
      전부 "무음"으로 오판되어 실제 발화가 잘리고 0.4~1.6초짜리 파편 클립이
      양산된다. word timestamp 신뢰도가 검증된 경우에만 켤 것.
    """
    _pad = pad if pad is not None else WORD_START_PAD_SEC
    _pad_end = pad_end if pad_end is not None else max(_pad, WORD_END_PAD_SEC)

    segments = json.loads(Path(words_json_path).read_text(encoding='utf-8'))

    spans = []
    split_count = 0
    for s in segments:
        if s['end'] - s['start'] < min_dur:
            continue
        if word_split:
            sub = split_segment_by_long_words(s, min_dur=min_dur)
            if len(sub) > 1:
                split_count += 1
                print(f'  [단어분리] {s["start"]:.1f}~{s["end"]:.1f}s → {len(sub)}개 | "{s.get("text", "").strip()[:50]}"')
        else:
            sub = [(s['start'], s['end'])]
        for ss, se in sub:
            spans.append((max(0.0, ss - _pad), se + _pad_end))

    if word_split:
        print(f'  단어 수준 분리: {split_count}개 세그먼트에서 sub-segment 생성')

    total_before = len(spans)
    if ng_spans:
        spans = apply_ng_filter(spans, ng_spans, threshold=ng_threshold)
        print(f'  NG 필터: {total_before}개 → {len(spans)}개')

    result = merge_gaps(spans, gap_sec=merge_gap, min_dur=min_dur)
    total_sec = sum(e - s for s, e in result)
    print(f'  Whisper 세그먼트 기반: {len(result)}개 구간, {total_sec:.0f}초 ({total_sec/60:.1f}분)')
    return result


def default_out_path(words_json: Path) -> Path:
    """<stem>_words.json → <stem>_segments.json (영상 옆에 저장, 영상별로 분리)."""
    name = words_json.name
    stem = name[:-len('_words.json')] if name.endswith('_words.json') else words_json.stem
    return words_json.with_name(stem + '_segments.json')


# ──────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='CapCut 컷편집 구간 생성')
    parser.add_argument('--words-json', required=True,
                        help='faster-whisper words.json 경로 (transcribe.py 출력)')
    parser.add_argument('--ng', default=None,
                        help='NG 로그 JSON {"ng_spans": [[s, e], ...]} (선택)')
    parser.add_argument('--out', default=None,
                        help='출력 JSON 경로 (기본: <stem>_segments.json, words.json 옆)')
    parser.add_argument('--ng-threshold', type=float, default=NG_REMOVE_THRESHOLD,
                        help=f'NG 비율이 이 이상이면 문장 통째로 제거 (기본: {NG_REMOVE_THRESHOLD} = 찍은 곳만 자름)')
    parser.add_argument('--merge-gap', type=float, default=MERGE_GAP_SEC,
                        help=f'갭 병합 임계값(초) (기본: {MERGE_GAP_SEC})')
    parser.add_argument('--pad-start', type=float, default=WORD_START_PAD_SEC,
                        help=f'클립 시작단 패딩(초) — 앞글자 잘림 방지 (기본: {WORD_START_PAD_SEC})')
    parser.add_argument('--pad-end', type=float, default=WORD_END_PAD_SEC,
                        help=f'클립 끝단 패딩(초) — 종결어미 잘림 방지 (기본: {WORD_END_PAD_SEC})')
    parser.add_argument('--word-split', action='store_true',
                        help='단어 수준 sub-segment 분리 활성화 (기본: 비활성화, 위험성은 docstring 참고)')
    args = parser.parse_args()

    words_json = Path(args.words_json)
    if not words_json.exists():
        raise SystemExit(f'❌ words.json 없음: {words_json}')

    ng_spans = []
    if args.ng and Path(args.ng).exists():
        ng_spans = json.loads(Path(args.ng).read_text(encoding='utf-8'))['ng_spans']
        print(f'  NG 로그: {len(ng_spans)}개 구간 ({args.ng})')
    elif args.ng:
        print(f'  ⚠ NG 로그 없음 ({args.ng}) → 문장 사이 침묵만 제거')

    print('=== Whisper 세그먼트 기반 클립 생성 ===')
    print(f'  words.json: {words_json}')
    print(f'  파라미터: NG_THRESHOLD={args.ng_threshold:.0%}, MERGE_GAP={args.merge_gap}s')

    spans = build_from_words_json(
        words_json, ng_spans=ng_spans,
        pad=args.pad_start, pad_end=args.pad_end,
        merge_gap=args.merge_gap, min_dur=MIN_SPEECH_SEC,
        ng_threshold=args.ng_threshold, word_split=args.word_split,
    )

    out = Path(args.out) if args.out else default_out_path(words_json)
    out.write_text(json.dumps([[s, e] for s, e in spans]), encoding='utf-8')
    print(f'\n저장: {out}')


if __name__ == '__main__':
    main()
