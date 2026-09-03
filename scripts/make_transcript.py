#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""
Vibecut — Claude가 읽을 transcript 생성 (정적 표시 포함)

{stem}_words.json + {stem}_audio.wav → {stem}_transcript.txt

왜 오디오까지 보는가 (실전 실패 사례):
  Whisper는 말이 멈춘 시간을 앞 단어의 길이에 흡수시킨다. 대본만 읽으면
  "지금 얼리버드 신청 중이니까"가 한 문장처럼 보이지만 실제로는 "신청" 뒤에
  1.9초 정적이 있었고, 그런 곳이 영상 하나에 5군데였다. ffmpeg silencedetect로
  실제 정적을 재서 그 자리에 ⏸ 표시를 끼워 넣으면 Claude가 NG로 잡을 수 있다.

출력 예:
  [00:36 (36.4~39.1s)] 개발자 멘토가 옆에서 직접 봐드립니다 한
      ⏸ 정적 3.9초 (39.1~43.0s)
  [00:43 (43.0~45.7s)] 달 20시간의 과정을 마치고 나면

사용법:
  uv run make_transcript.py <영상>_words.json [--audio <영상>_audio.wav] [--min-silence 0.7]
"""

import argparse
import json
import re
import shutil
import subprocess
from pathlib import Path

MIN_SILENCE_SEC = 0.7      # 이 이상 이어지는 정적만 표시
NOISE_DB = -30             # 배경 소음이 있는 촬영에서 -35dB는 못 잡았음 (실전)


def detect_silences(wav: Path, noise_db: int = NOISE_DB, min_dur: float = 0.4) -> list[tuple[float, float]]:
    """ffmpeg silencedetect 결과 [(start, end), ...]. ffmpeg가 없으면 []."""
    if not shutil.which("ffmpeg"):
        return []
    # -v error 로 하면 silencedetect 로그(info 레벨)가 안 나온다 (실전에서 한 번 헤맴)
    r = subprocess.run(
        ["ffmpeg", "-nostats", "-loglevel", "info", "-i", str(wav),
         "-af", f"silencedetect=noise={noise_db}dB:d={min_dur}", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    starts = [float(m) for m in re.findall(r"silence_start: ([\d.]+)", r.stderr)]
    ends = [float(m) for m in re.findall(r"silence_end: ([\d.]+)", r.stderr)]
    merged: list[tuple[float, float]] = []
    for s, e in zip(starts, ends):
        if merged and s - merged[-1][1] < 0.15:   # 임계값 근처에서 잘게 끊긴 정적을 하나로
            merged[-1] = (merged[-1][0], e)
        else:
            merged.append((s, e))
    return merged


def split_words_by_silence(words: list[dict], silences: list[tuple[float, float]],
                           min_silence: float) -> list[tuple[list[dict], tuple[float, float] | None]]:
    """단어 목록을 정적 지점에서 나눈다. 반환: [(단어들, 뒤따르는 정적 또는 None), ...]

    단어는 시작 시각이 정적 시작보다 앞이면 정적 앞 그룹에 속한다 (Whisper가
    정적을 그 단어 길이에 흡수시켰어도 발음 자체는 정적 앞에 끝났기 때문).
    정적이 첫 단어 시작 직후(예: 0.08초 뒤)에 시작해도 실제 정적이다 — Whisper의
    단어 시작이 약간 이르기 때문. 그래서 가장자리 여유를 두지 않는다.
    """
    if not words:
        return []
    first, last_start = words[0]["start"], words[-1]["start"]
    inner = [(s, e) for s, e in silences
             if e - s >= min_silence and first <= s < last_start]
    groups, cur, i = [], [], 0
    for w in words:
        if i < len(inner) and w["start"] >= inner[i][0]:
            # 이 단어는 정적 뒤에서 시작 → 앞 그룹을 닫는다
            if cur:
                groups.append((cur, inner[i]))
                cur = []
            i += 1
            while i < len(inner) and w["start"] >= inner[i][1]:
                i += 1
        cur.append(w)
    if cur:
        groups.append((cur, None))
    return groups


def fallback_split(words: list[dict]) -> list[tuple[list[dict], tuple[float, float] | None]]:
    """ffmpeg가 없을 때: 단어 사이 간격/비정상적으로 긴 단어로 정적을 추정."""
    if len(words) < 3:
        return [(words, None)] if words else []
    durs = [w["end"] - w["start"] for w in words]
    trimmed = sorted(durs)[:max(1, int(len(durs) * 0.7))]
    thr = max(2.5, sum(trimmed) / len(trimmed) * 3.0)
    groups, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        if i == len(words) - 1:
            break
        nxt = words[i + 1]
        if durs[i] > thr:
            groups.append((cur, (w["start"] + 0.3, w["end"])))
            cur = []
        elif nxt["start"] - w["end"] > 1.5:
            groups.append((cur, (w["end"], nxt["start"])))
            cur = []
    if cur:
        groups.append((cur, None))
    return groups


def fmt_line(start: float, end: float, text: str) -> str:
    m, s = divmod(int(start), 60)
    return f"[{m:02d}:{s:02d} ({start:.1f}~{end:.1f}s)] {text}"


def gap_marker(start: float, end: float, silences: list[tuple[float, float]] | None) -> str:
    """문장 사이 빈 구간이 정말 조용했는지, 소리는 있는데 Whisper가 놓친 건지 구분.

    실전에서 20초 넘는 공백이 "다시 처음부터 할게요" 같은 NG 잡담이었는데
    words.json에는 아무것도 없었다. 정적이 아니면 Claude가 원본을 확인하게 표시한다.
    """
    dur = end - start
    if silences is not None:
        quiet = sum(max(0.0, min(e, end) - max(s, start)) for s, e in silences)
        if quiet < dur * 0.6:
            return f"    🔊 인식 안 된 소리 {dur:.1f}초 ({start:.1f}~{end:.1f}s) — 정적 아님, NG 잡담일 수 있음"
    return f"    ⏸ 정적 {dur:.1f}초 ({start:.1f}~{end:.1f}s)"


def build_transcript(segments: list[dict], silences: list[tuple[float, float]] | None,
                     min_silence: float = MIN_SILENCE_SEC) -> str:
    lines = []
    prev_end = None
    last_sil = (-1.0, -1.0)   # 직전에 표시한 정적 — 문장 사이 공백이 그 안이면 중복 표시하지 않음
    for seg in segments:
        words = [w for w in seg.get("words", []) if "start" in w and "end" in w]
        if prev_end is not None and seg["start"] - prev_end >= 1.0 \
                and not (prev_end >= last_sil[0] - 0.2 and seg["start"] <= last_sil[1] + 0.2):
            lines.append(gap_marker(prev_end, seg["start"], silences))
        if not words:
            lines.append(fmt_line(seg["start"], seg["end"], seg["text"].strip()))
            prev_end = seg["end"]
            continue
        groups = (split_words_by_silence(words, silences, min_silence)
                  if silences is not None else fallback_split(words))
        for ws, sil in groups:
            start = ws[0]["start"]
            end = min(ws[-1]["end"], sil[0]) if sil else ws[-1]["end"]
            lines.append(fmt_line(start, max(end, start + 0.05), "".join(w["word"] for w in ws).strip()))
            if sil:
                lines.append(f"    ⏸ 정적 {sil[1] - sil[0]:.1f}초 ({sil[0]:.1f}~{sil[1]:.1f}s)")
                last_sil = sil
        # Whisper는 단어 끝이 세그먼트 끝을 넘기도 한다 → 둘 중 늦은 쪽
        prev_end = max(seg["end"], words[-1]["end"])
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="words.json → 정적 표시가 있는 transcript")
    ap.add_argument("words_json", help="<영상>_words.json")
    ap.add_argument("--audio", default=None, help="<영상>_audio.wav (기본: words.json 옆에서 자동 탐색)")
    ap.add_argument("--min-silence", type=float, default=MIN_SILENCE_SEC)
    ap.add_argument("--out", default=None, help="기본: <영상>_transcript.txt")
    args = ap.parse_args()

    wp = Path(args.words_json)
    stem = wp.name[:-len("_words.json")] if wp.name.endswith("_words.json") else wp.stem
    segments = json.loads(wp.read_text(encoding="utf-8"))

    wav = Path(args.audio) if args.audio else wp.with_name(stem + "_audio.wav")
    silences = None
    if wav.exists():
        silences = detect_silences(wav)
        if not silences and not shutil.which("ffmpeg"):
            silences = None
            print("  ⚠ ffmpeg 없음 → 단어 간격으로 정적을 추정합니다 (정확도 낮음)")
        else:
            print(f"  정적 측정: {len(silences)}개 구간 ({wav.name}, {NOISE_DB}dB)")
    else:
        print(f"  ⚠ {wav.name} 없음 → 단어 간격으로 정적을 추정합니다 (정확도 낮음)")

    text = build_transcript(segments, silences, args.min_silence)
    out = Path(args.out) if args.out else wp.with_name(stem + "_transcript.txt")
    out.write_text(text, encoding="utf-8")
    n_sil = text.count("⏸")
    print(f"  원본 세그먼트 {len(segments)}개 → 줄 {len(text.splitlines()) - n_sil}개, 정적 표시 {n_sil}개")
    print(f"저장: {out}\n")
    print(text)


if __name__ == "__main__":
    main()
