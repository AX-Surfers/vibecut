#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""
Vibecut — CapCut 컷편집 구간 생성 스크립트 (개선판, 의존성 없음)

사용자 편집 기준 분석을 반영한 3가지 개선:
  1. NG 완화: NG 구간이 발화의 50% 미만이면 살림 (이진법→비율 기반)
  2. 갭 병합 강화: 인접 구간 사이 0.5초 이하 갭은 병합 (기존 0.2초)
  3. 필러 필터: whisper 전사 기반 내용 없는 짧은 구간 제거

사용법:
  python3 make_segments.py \\
    --speech /tmp/speech_segments.json \\
    --ng /path/to/hwp(원본)_ng_log.json \\
    [--transcript /tmp/transcript.json] \\
    [--out /tmp/final_segments.json]

  python3 make_segments.py --help
"""

import argparse
import json
import re
from pathlib import Path

# ──────────────────────────────────────────────
# 파라미터 (사용자 편집 패턴 기반으로 조정)
# ──────────────────────────────────────────────

# [개선 1] NG 점유 비율 임계값
# speech 구간에서 NG가 이 비율 이상이면 해당 구간 제거
# 기존: NG가 조금이라도 있으면 제거 (= 0.0 임계값)
# 개선: 50% 이상 차지할 때만 제거 → 사용자의 34% NG 살리기 반영
NG_REMOVE_THRESHOLD = 0.50

# [개선 1-b] NG 구간 앞뒤 분리 시 최소 잔여 길이
# 남은 앞/뒤 조각이 이 길이보다 짧으면 버림
MIN_RESIDUAL_SEC = 0.3

# [개선 2] 갭 병합 임계값 (초)
# 기존: 0.2초, 개선: 0.5초 (사용자: 216 덩어리, 내것: 326 덩어리 → 병합 강화)
MERGE_GAP_SEC = 0.5

# 최소 발화 구간 길이 (초)
# 병합 후에도 이 길이보다 짧으면 버림
MIN_SPEECH_SEC = 0.3

# [개선 3] 필러 패턴 (whisper 전사 기반)
# 전체 텍스트가 이 패턴만 있으면 저품질 구간으로 판단
FILLER_PATTERNS = [
    r'^[네예아어음 ]+$',                    # 단순 감탄사
    r'^(네|예|아|어|음|그|이제|그래서|근데){1,3}[\.이]?$',  # 짧은 접속사
    r'^[\.]{1,5}$',                          # 점
]


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
    total_ng = sum(overlap(ss, se, ns, ne) for ns, ne in ng_spans)
    return total_ng / dur


# ──────────────────────────────────────────────
# 개선 1: NG 처리 (비율 기반)
# ──────────────────────────────────────────────

def apply_ng_filter(speech_spans, ng_spans, threshold=NG_REMOVE_THRESHOLD,
                    min_residual=MIN_RESIDUAL_SEC):
    """
    각 speech 구간에서 NG 비율에 따라 처리:
      - NG 비율 >= threshold → 구간 제거
      - NG 비율 < threshold  → 구간 유지 (NG 포함)
        단, NG 구간을 잘라낸 뒤 남은 조각이 min_residual 이상인 것만 유지
    """
    result = []

    for ss, se in speech_spans:
        ratio = ng_ratio(ss, se, ng_spans)

        if ratio >= threshold:
            # NG 비율이 높음 → 제거
            continue

        if ratio == 0.0:
            # NG 없음 → 그대로 유지
            result.append((ss, se))
            continue

        # NG가 일부 있지만 threshold 미만 → NG 구간만 잘라내고 조각 유지
        # speech 구간 내 NG 구간 찾기
        ng_in_range = sorted(
            [(max(ss, ns), min(se, ne)) for ns, ne in ng_spans
             if overlap(ss, se, ns, ne) > 0]
        )

        # NG 구간을 빼고 남은 조각들
        pieces = []
        cur = ss
        for ns, ne in ng_in_range:
            if cur < ns - 0.01:
                pieces.append((cur, ns))
            cur = max(cur, ne)
        if cur < se - 0.01:
            pieces.append((cur, se))

        # min_residual 이상인 조각만 추가
        for ps, pe in pieces:
            if pe - ps >= min_residual:
                result.append((ps, pe))

    return result


# ──────────────────────────────────────────────
# 개선 2: 갭 병합
# ──────────────────────────────────────────────

def merge_gaps(spans, gap_sec=MERGE_GAP_SEC, min_dur=MIN_SPEECH_SEC):
    """
    인접한 구간 사이 갭이 gap_sec 이하이면 병합.
    병합 후 min_dur 미만인 구간은 제거.
    """
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
# 개선 3: 필러 필터 (whisper 전사 기반)
# ──────────────────────────────────────────────

def build_wav_to_orig_map(speech_spans):
    """
    speech_only.wav 오프셋 → 원본 영상 시간 매핑 테이블 구축
    """
    table = []
    acc = 0.0
    for s, e in sorted(speech_spans):
        dur = e - s
        table.append({'orig_s': s, 'orig_e': e, 'wav_s': acc, 'wav_e': acc + dur})
        acc += dur
    return table


def wav_to_orig(wav_sec, table):
    """whisper의 wav 오프셋(초) → 원본 영상 시간(초)"""
    for seg in table:
        if seg['wav_s'] <= wav_sec <= seg['wav_e'] + 0.01:
            offset = wav_sec - seg['wav_s']
            return seg['orig_s'] + offset
    return None


def is_filler(text):
    """전사 텍스트가 의미 없는 필러인지 판단"""
    t = text.strip()
    if len(t) < 2:
        return True
    for pat in FILLER_PATTERNS:
        if re.match(pat, t):
            return True
    return False


def apply_filler_filter(spans, transcript_data, speech_spans, max_dur=5.0):
    """
    whisper 전사에서 필러로 판단된 짧은 구간 제거.
    max_dur: 이 길이보다 긴 구간은 필러로 처리하지 않음.
    """
    if not transcript_data:
        return spans

    wav_map = build_wav_to_orig_map(speech_spans)
    transcription = transcript_data.get('transcription', [])

    # 필러로 판단된 원본 구간 수집
    filler_spans = []
    for seg in transcription:
        text = seg.get('text', '').strip()
        if not is_filler(text):
            continue
        ws = seg['offsets']['from'] / 1000.0
        we = seg['offsets']['to'] / 1000.0
        if we - ws > max_dur:
            continue
        os_ = wav_to_orig(ws, wav_map)
        oe = wav_to_orig(we, wav_map)
        if os_ is not None and oe is not None:
            filler_spans.append((os_, oe))

    if not filler_spans:
        return spans

    print(f'  [필러 필터] 감지된 필러 구간: {len(filler_spans)}개')

    # 필러 구간 제거 (NG 처리와 동일한 로직, 더 공격적: threshold=0.8)
    return apply_ng_filter(spans, filler_spans, threshold=0.8, min_residual=0.3)


# ──────────────────────────────────────────────
# 메인 파이프라인
# ──────────────────────────────────────────────

def make_segments(speech_path, ng_path=None, transcript_path=None, out_path=None,
                  verbose=True, ng_threshold=None, merge_gap=None):

    # 파라미터 적용 (인자로 받으면 전역값 오버라이드)
    _ng_threshold = ng_threshold if ng_threshold is not None else NG_REMOVE_THRESHOLD
    _merge_gap    = merge_gap    if merge_gap    is not None else MERGE_GAP_SEC

    def log(msg):
        if verbose:
            print(msg)

    # ── 로드 ──
    with open(speech_path) as f:
        speech_spans = json.load(f)

    ng_spans = []
    if ng_path and Path(ng_path).exists():
        with open(ng_path) as f:
            ng_data = json.load(f)
        ng_spans = ng_data['ng_spans']
        log(f'NG 로그: {len(ng_spans)}개 구간 로드')
    else:
        log('NG 로그 없음 → 무음 제거만 적용')

    transcript_data = None
    if transcript_path and Path(transcript_path).exists():
        with open(transcript_path) as f:
            transcript_data = json.load(f)

    # 통계 함수
    def stats(spans, label):
        total = sum(e - s for s, e in spans)
        log(f'  {label}: {len(spans)}개 구간, 총 {total:.0f}초 ({total/60:.1f}분)')

    log('=== 편집 구간 생성 (개선판) ===')
    log(f'파라미터: NG_THRESHOLD={_ng_threshold:.0%}, MERGE_GAP={_merge_gap}s')
    log('')

    # ── Step 1: 무음 제거된 발화 구간 ──
    log('[1단계] 무음 제거 완료된 발화 구간')
    stats(speech_spans, '발화')

    # ── Step 2: NG 필터 (비율 기반) ──
    log(f'\n[2단계] NG 필터 (NG 비율 {_ng_threshold:.0%} 이상만 제거)')
    spans = apply_ng_filter(speech_spans, ng_spans, threshold=_ng_threshold)
    stats(spans, '처리 후')

    removed_ng = [(ss, se) for ss, se in speech_spans
                  if ng_ratio(ss, se, ng_spans) >= _ng_threshold]
    kept_partial_ng = [(ss, se) for ss, se in speech_spans
                       if 0 < ng_ratio(ss, se, ng_spans) < _ng_threshold]
    log(f'  → 완전 제거: {len(removed_ng)}개, 부분 NG 유지: {len(kept_partial_ng)}개')

    # ── Step 3: 필러 필터 ──
    if transcript_data:
        log('\n[3단계] 필러 필터 (whisper 전사 기반)')
        prev_count = len(spans)
        spans = apply_filler_filter(spans, transcript_data, speech_spans)
        log(f'  → {prev_count}개 → {len(spans)}개 ({prev_count - len(spans)}개 제거)')
        stats(spans, '처리 후')
    else:
        log('\n[3단계] 필러 필터 생략 (transcript 없음)')

    # ── Step 4: 갭 병합 ──
    log(f'\n[4단계] 갭 병합 ({_merge_gap}초 이하 갭 병합)')
    spans = merge_gaps(spans, gap_sec=_merge_gap)
    stats(spans, '병합 후')

    # ── 최종 통계 ──
    # 원본 길이: speech_spans 마지막 끝 시간 기준 (영상 길이 근사값)
    orig_dur = max(e for _, e in speech_spans) if speech_spans else 1
    total = sum(e - s for s, e in spans)
    log('\n=== 결과 ===')
    log(f'원본:  {orig_dur/60:.1f}분 (발화 구간 기준)')
    log(f'결과:  {total/60:.1f}분 ({len(spans)}개 구간, {(1 - total/orig_dur)*100:.0f}% 제거)')

    # ── 저장 ──
    out = out_path or '/tmp/final_segments.json'
    with open(out, 'w') as f:
        json.dump([[s, e] for s, e in spans], f)
    log(f'\n저장: {out}')

    return spans


# ──────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='CapCut 컷편집 구간 생성 (개선판)')
    parser.add_argument('--speech', default='/tmp/speech_segments.json',
                        help='무음 제거된 발화 구간 JSON')
    parser.add_argument('--ng', default=None,
                        help='NG 로그 JSON (선택)')
    parser.add_argument('--transcript', default='/tmp/transcript.json',
                        help='whisper 전사 결과 JSON (선택)')
    parser.add_argument('--out', default='/tmp/final_segments.json',
                        help='출력 JSON 경로')
    parser.add_argument('--ng-threshold', type=float, default=NG_REMOVE_THRESHOLD,
                        help=f'NG 제거 비율 임계값 (기본: {NG_REMOVE_THRESHOLD})')
    parser.add_argument('--merge-gap', type=float, default=MERGE_GAP_SEC,
                        help=f'갭 병합 임계값(초) (기본: {MERGE_GAP_SEC})')
    args = parser.parse_args()

    # 파라미터를 make_segments에 직접 전달
    # 전역 변수 오버라이드 대신 함수 파라미터로 처리
    make_segments(
        speech_path=args.speech,
        ng_path=args.ng,
        transcript_path=args.transcript,
        out_path=args.out,
        ng_threshold=args.ng_threshold,
        merge_gap=args.merge_gap,
    )


if __name__ == '__main__':
    main()
