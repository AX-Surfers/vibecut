#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "faster-whisper>=1.0.0",
# ]
# ///
"""
Vibecut — NG 구간 자동 감지

말 실수·재시작 패턴과 긴 침묵 후 재시작 패턴을 전사 결과에서 자동 탐지.

사용법:
  uv run scripts/detect_ng.py <video.mov>
  uv run scripts/detect_ng.py <video.mov> --speech /tmp/speech_segments.json
  uv run scripts/detect_ng.py <video.mov> --words <stem>_words.json
  uv run scripts/detect_ng.py <video.mov> --out /tmp/ng_log.json
  uv run scripts/detect_ng.py <video.mov> --model small  # Whisper 모델 선택

출력:
  {stem}_ng_log.json — {"ng_spans": [[start_sec, end_sec], ...]}
  → make_segments.py --ng 에 그대로 전달 가능
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

# ──────────────────────────────────────────
# NG 감지 파라미터
# ──────────────────────────────────────────

# 패턴 A: NG 키워드
NG_KEYWORDS: list[str] = [
    "잠깐", "잠깐만", "잠시만", "다시", "아니", "아니요", "아니다",
    "죄송", "NG", "컷", "틀렸", "실수", "다시 해", "다시 한번",
]

# 패턴 B: 반복 구절 Jaccard 임계값
# 사용자 편집 패턴 학습: 0.60→0.45 (링크 복사, 자동화 등 유사도 0.5 반복 구절 포착)
REPEAT_JACCARD_THRESHOLD: float = 0.45

# 패턴 C: 급정지 파라미터
# 사용자 편집 패턴 학습: SHORT_SPEECH_MAX 2.0→3.0, MIN_WORDS_COMPLETE 3→4
SHORT_SPEECH_MAX: float = 3.0   # 발화 구간 최대 길이 (초)
LONG_SILENCE_MIN: float = 1.5   # 이후 최소 침묵 길이 (초)
MIN_WORDS_COMPLETE: int  = 4    # 완전한 발화로 보는 최소 단어 수

# NG 구간 앞뒤 패딩 (경계 오차 보정)
NG_PADDING: float = 0.1


# ──────────────────────────────────────────
# Whisper 전사
# ──────────────────────────────────────────

def extract_audio(video_path: Path) -> Path:
    """영상에서 16kHz mono WAV 오디오 추출 (Whisper 최적 포맷).

    반환: 추출된 WAV 경로 ({stem}_audio.wav)
    """
    wav_path = video_path.with_name(video_path.stem + "_audio.wav")
    if wav_path.exists():
        print(f"  오디오 캐시 사용: {wav_path.name}")
        return wav_path
    print(f"  오디오 추출 중: {video_path.name} → {wav_path.name}")
    result = subprocess.run(
        ["ffmpeg", "-y", "-i", str(video_path),
         "-vn", "-ar", "16000", "-ac", "1", str(wav_path)],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print("ffmpeg 오류:", result.stderr[-400:], file=sys.stderr)
        sys.exit(1)
    import os
    size_mb = os.path.getsize(wav_path) / 1024 / 1024
    print(f"  → {wav_path.name} ({size_mb:.1f}MB)")
    return wav_path


def transcribe(audio_path: Path, model_name: str = "tiny") -> list[dict]:
    """faster-whisper로 한국어 전사 + 단어 타임스탬프.

    audio_path: WAV 파일 경로 (영상이 아닌 오디오를 직접 받음)
    NG 감지 목적이므로 tiny 모델 기본값 (빠른 속도 우선).
    반환: [{start, end, text, words: [{start, end, word}, ...]}, ...]
    """
    py = sys.executable
    print(f"  Whisper 전사 중 (모델: {model_name}, 대상: {audio_path.name}) ...")
    script = (
        "from faster_whisper import WhisperModel\nimport json\n"
        f'model = WhisperModel("{model_name}", device="cpu", compute_type="int8")\n'
        f'segs, _ = model.transcribe("{audio_path}", language="ko", beam_size=1, word_timestamps=True)\n'
        "result = []\n"
        "for s in segs:\n"
        "    words = [{'start': w.start, 'end': w.end, 'word': w.word} for w in (s.words or [])]\n"
        "    result.append({'start': s.start, 'end': s.end, 'text': s.text.strip(), 'words': words})\n"
        "print(json.dumps(result, ensure_ascii=False))"
    )
    result = subprocess.run([py, "-c", script], capture_output=True, text=True)
    if result.returncode != 0:
        print("Whisper 오류:", result.stderr[-800:], file=sys.stderr)
        sys.exit(1)
    for line in reversed(result.stdout.strip().splitlines()):
        if line.strip().startswith("["):
            segments = json.loads(line.strip())
            total_words = sum(len(s.get("words", [])) for s in segments)
            print(f"  → {len(segments)}개 구간 / {total_words}개 단어 인식 완료")
            return segments
    print("전사 결과 파싱 실패", file=sys.stderr)
    sys.exit(1)


def load_or_transcribe(video_path: Path, words_path: Path | None,
                       model_name: str) -> list[dict]:
    """words.json 캐시 우선, 없으면 전사.

    우선순위:
      1. --words 인자로 명시된 파일
      2. {stem}_words.json (add-subtitles 원본 캐시)
      3. faster-whisper 직접 전사 후 캐시 저장
    """
    # 1. 명시된 파일
    if words_path and words_path.exists():
        print(f"  단어 캐시 사용: {words_path.name}")
        return json.loads(words_path.read_text(encoding="utf-8"))

    # 2. 자동 탐색 (_edited_words.json은 타임스탬프 불일치로 제외)
    candidate = video_path.with_name(video_path.stem + "_words.json")
    if candidate.exists():
        print(f"  단어 캐시 재사용: {candidate.name}")
        return json.loads(candidate.read_text(encoding="utf-8"))

    # 3. 오디오 추출 후 전사
    wav_path = extract_audio(video_path)
    segments = transcribe(wav_path, model_name)
    cache = video_path.with_name(video_path.stem + "_words.json")
    cache.write_text(json.dumps(segments, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  캐시 저장: {cache.name}")
    return segments


# ──────────────────────────────────────────
# 패턴 A: NG 키워드 감지
# ──────────────────────────────────────────

def detect_keyword_ng(segments: list[dict]) -> list[tuple[float, float]]:
    """NG 키워드가 포함된 세그먼트를 NG 구간으로 마킹."""
    ng_spans: list[tuple[float, float]] = []
    for seg in segments:
        text = seg.get("text", "").strip()
        for kw in NG_KEYWORDS:
            if kw in text:
                ng_spans.append((seg["start"], seg["end"]))
                break
    return ng_spans


# ──────────────────────────────────────────
# 패턴 B: 반복 구절 감지 (Jaccard Similarity)
# ──────────────────────────────────────────

def jaccard(text_a: str, text_b: str) -> float:
    """어절(공백 분리) 기준 Jaccard 유사도."""
    a = set(text_a.strip().split())
    b = set(text_b.strip().split())
    if not a and not b:
        return 0.0
    return len(a & b) / len(a | b)


def detect_repeat_ng(segments: list[dict],
                     threshold: float = REPEAT_JACCARD_THRESHOLD) -> list[tuple[float, float]]:
    """인접 세그먼트 간 유사도 >= threshold → 앞 세그먼트 NG.

    같은 내용을 두 번 말한 경우 앞의 것(실수한 버전)을 제거.
    """
    ng_spans: list[tuple[float, float]] = []
    for i in range(len(segments) - 1):
        sim = jaccard(segments[i].get("text", ""), segments[i + 1].get("text", ""))
        if sim >= threshold:
            ng_spans.append((segments[i]["start"], segments[i]["end"]))
    return ng_spans


# ──────────────────────────────────────────
# 패턴 C: 짧은 발화 + 긴 침묵 급정지
# ──────────────────────────────────────────

def detect_abrupt_stop_ng(segments: list[dict],
                          speech_spans: list[list[float]]) -> list[tuple[float, float]]:
    """짧은 발화 + 단어 부족 + 이후 긴 침묵 → 말하다 멈춘 NG 후보.

    speech_spans: [[start_sec, end_sec], ...] — 발화 구간 (무음 제거 후)
    침묵 구간을 계산하기 위해 필요. 없으면 호출하지 않음.
    """
    if not speech_spans:
        return []

    sorted_spans = sorted(speech_spans)
    ng_spans: list[tuple[float, float]] = []

    for seg in segments:
        dur = seg["end"] - seg["start"]
        if dur >= SHORT_SPEECH_MAX:
            continue

        word_count = len(seg.get("words", []))
        if word_count >= MIN_WORDS_COMPLETE:
            continue  # 단어 수 충분 → 완전한 발화

        # 현재 세그먼트 이후 첫 번째 발화 구간 시작 찾기
        next_speech_start = None
        for ss, _se in sorted_spans:
            if ss > seg["end"] + 0.1:
                next_speech_start = ss
                break

        if next_speech_start is None:
            continue  # 이후 발화 없음

        silence_dur = next_speech_start - seg["end"]
        if silence_dur >= LONG_SILENCE_MIN:
            ng_spans.append((seg["start"], seg["end"]))

    return ng_spans


# ──────────────────────────────────────────
# 병합 및 출력
# ──────────────────────────────────────────

def merge_ng_spans(spans: list[tuple[float, float]],
                   padding: float = NG_PADDING) -> list[list[float]]:
    """NG 구간 정렬 → 패딩 → 중복 병합."""
    if not spans:
        return []

    padded = [(max(0.0, s - padding), e + padding) for s, e in spans]
    padded.sort()

    merged: list[list[float]] = [[padded[0][0], padded[0][1]]]
    for s, e in padded[1:]:
        if s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])

    return merged


# ──────────────────────────────────────────
# main
# ──────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="NG 구간 자동 감지 → ng_log.json 생성")
    parser.add_argument("video", help="입력 영상 파일 (.mov / .mp4)")
    parser.add_argument("--words", default=None,
                        help="Whisper words.json 경로 (지정 시 재사용, 없으면 자동 탐색 후 전사)")
    parser.add_argument("--speech", default=None,
                        help="speech_segments.json 경로 (패턴 C에 사용)")
    parser.add_argument("--out", default=None,
                        help="출력 ng_log.json 경로 (기본: {stem}_ng_log.json)")
    parser.add_argument("--model", default="tiny",
                        choices=["tiny", "base", "small", "medium", "large-v3"],
                        help="Whisper 모델 (기본: tiny, NG 감지엔 충분)")
    parser.add_argument("--jaccard", type=float, default=REPEAT_JACCARD_THRESHOLD,
                        help=f"반복 구절 유사도 임계값 (기본: {REPEAT_JACCARD_THRESHOLD})")
    args = parser.parse_args()

    video_path = Path(args.video).resolve()
    if not video_path.exists():
        print(f"오류: 파일 없음 — {video_path}", file=sys.stderr)
        sys.exit(1)

    words_path = Path(args.words).resolve() if args.words else None
    out_path   = Path(args.out).resolve() if args.out \
                 else video_path.with_name(video_path.stem + "_ng_log.json")

    # speech_spans 로드 (패턴 C용)
    speech_spans: list[list[float]] = []
    if args.speech:
        sp = Path(args.speech).resolve()
        if sp.exists():
            speech_spans = json.loads(sp.read_text(encoding="utf-8"))
            print(f"  발화 구간 로드: {len(speech_spans)}개 ({sp.name})")
        else:
            print(f"  경고: speech 파일 없음 — 패턴 C 건너뜀 ({sp})")

    print("\n[1/2] 전사 로드")
    segments = load_or_transcribe(video_path, words_path, args.model)

    print("\n[2/2] NG 패턴 감지")

    kw_ng   = detect_keyword_ng(segments)
    rep_ng  = detect_repeat_ng(segments, threshold=args.jaccard)
    stop_ng = detect_abrupt_stop_ng(segments, speech_spans)

    print(f"  패턴 A (키워드):   {len(kw_ng)}개")
    print(f"  패턴 B (반복 구절): {len(rep_ng)}개  (Jaccard ≥ {args.jaccard})")
    print(f"  패턴 C (급정지):   {len(stop_ng)}개")

    all_ng = kw_ng + rep_ng + stop_ng
    merged = merge_ng_spans(all_ng)

    total_ng_sec = sum(e - s for s, e in merged)
    print(f"\n  → 최종 NG 구간: {len(merged)}개, 총 {total_ng_sec:.1f}초")

    if merged:
        print("\n  상위 10개:")
        for s, e in merged[:10]:
            print(f"    {s:7.2f}s ~ {e:7.2f}s  ({e-s:.1f}초)")

    out_path.write_text(
        json.dumps({"ng_spans": merged}, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    print(f"\n✓ 저장: {out_path}")
    print(f"  → make_segments.py --ng {out_path} 으로 전달하세요")


if __name__ == "__main__":
    main()
