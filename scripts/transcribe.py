#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "faster-whisper>=1.0.0",
# ]
# ///
"""
Vibecut — Whisper 전사 (단어 타임스탬프 포함)

영상/오디오 → {stem}_audio.wav → faster-whisper → {stem}_words.json
NG 판단은 Claude가 transcript를 읽고 하므로 (make_transcript.py), 이 스크립트는
전사와 결과 건전성 점검만 맡는다. (예전 detect_ng.py의 키워드/Jaccard 감지는 폐기)

사용법:
  uv run transcribe.py <video.mov> [--model large-v3-turbo] [--force]
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

DEFAULT_MODEL = "large-v3-turbo"


def audio_path_for(video_path: Path) -> Path:
    return video_path.with_name(video_path.stem + "_audio.wav")


def words_path_for(video_path: Path) -> Path:
    return video_path.with_name(video_path.stem + "_words.json")


def extract_audio(video_path: Path) -> Path:
    """영상에서 16kHz mono WAV 추출 (Whisper 최적 포맷). 이미 있으면 재사용."""
    wav_path = audio_path_for(video_path)
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
    print(f"  → {wav_path.name} ({os.path.getsize(wav_path) / 1024 / 1024:.1f}MB)")
    return wav_path


def transcribe(audio_path: Path, model_name: str = DEFAULT_MODEL) -> list[dict]:
    """faster-whisper 한국어 전사. 반환: [{start, end, text, words:[{start,end,word}]}]"""
    py = sys.executable
    print(f"  Whisper 전사 중 (모델: {model_name}, 대상: {audio_path.name}) ...")
    script = (
        "from faster_whisper import WhisperModel\nimport json\n"
        f'model = WhisperModel("{model_name}", device="cpu", compute_type="int8", cpu_threads=0, num_workers=4)\n'
        f'segs, _ = model.transcribe("{audio_path}", language="ko", beam_size=5, best_of=5,'
        f' word_timestamps=True, vad_filter=True,'
        f' vad_parameters={{"min_silence_duration_ms":500,"speech_pad_ms":200}},'
        f' temperature=[0.0,0.2,0.4,0.6,0.8,1.0], condition_on_previous_text=True)\n'
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
            return json.loads(line.strip())
    print("전사 결과 파싱 실패", file=sys.stderr)
    sys.exit(1)


def audio_duration(wav_path: Path) -> float | None:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", str(wav_path)], capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return None


def sanity_check(segments: list[dict], duration: float | None) -> None:
    """긴 구간을 통째로 놓쳤거나 단어 밀도가 낮으면 경고 (커스텀 모델 실패 사례)."""
    n_words = sum(len(s.get("words", [])) for s in segments)
    print(f"  → {len(segments)}개 구간 / {n_words}개 단어 인식 완료")
    if not segments:
        print("  ⚠ 인식된 발화가 없습니다. 오디오/모델을 확인하세요.")
        return
    gaps = [(a["end"], b["start"]) for a, b in zip(segments, segments[1:]) if b["start"] - a["end"] > 20]
    if duration and duration - segments[-1]["end"] > 20:
        gaps.append((segments[-1]["end"], duration))
    for s, e in gaps:
        print(f"  ⚠ {s/60:.1f}분~{e/60:.1f}분 사이 {e-s:.0f}초 동안 인식된 말이 없습니다 — "
              "실제로 조용했는지 확인하세요 (모델이 구간을 통째로 놓쳤을 수 있음)")
    if duration and n_words / max(duration / 60, 1e-6) < 30:
        print("  ⚠ 분당 단어 수가 30개 미만입니다 — 인식률이 낮을 수 있으니 transcript를 원본과 대조하세요")


def main():
    parser = argparse.ArgumentParser(description="Whisper 전사 → {stem}_words.json")
    parser.add_argument("video", help="입력 영상/오디오 파일")
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help=f"Whisper 모델 (기본: {DEFAULT_MODEL}). 커스텀 HF 모델은 존재·CTranslate2 호환을 먼저 확인할 것")
    parser.add_argument("--force", action="store_true", help="캐시가 있어도 다시 전사")
    args = parser.parse_args()

    video_path = Path(args.video).resolve()
    if not video_path.exists():
        raise SystemExit(f"오류: 파일 없음 — {video_path}")
    out = words_path_for(video_path)

    if out.exists() and not args.force:
        print(f"  단어 캐시 재사용: {out.name} (다시 전사하려면 --force)")
        segments = json.loads(out.read_text(encoding="utf-8"))
    else:
        wav = extract_audio(video_path)
        segments = transcribe(wav, args.model)
        out.write_text(json.dumps(segments, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  캐시 저장: {out.name}")

    wav = audio_path_for(video_path)
    sanity_check(segments, audio_duration(wav) if wav.exists() else None)
    print(f"✓ {out}")


if __name__ == "__main__":
    main()
