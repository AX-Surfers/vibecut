#!/usr/bin/env python3
"""말소리 마스크 — 실제 오디오를 20ms 칸으로 나눠 '말하는 칸 / 조용한 칸'을 가른다.

왜 ffmpeg silencedetect(-30dB)가 아닌가 (실전 실패 사례):
  노이즈 게이트가 걸린 화면녹화 오디오는 말이 -22~-30dB, 음절 사이 틈이 -60~-120dB였다.
  -30dB로 재면 구절 속 틈까지 정적으로 잡혀 자막·컷이 말 도중에 끊겼다. -45dB면 깔끔히 갈린다.
"""
import subprocess
import wave
from pathlib import Path

import numpy as np

HOP = 0.02
NOISE_DB = -45


def audio_for(video: Path) -> Path:
    """transcribe.py가 만든 {stem}_audio.wav를 쓰고, 없으면 같은 형식(16kHz mono)으로 뽑아 둔다."""
    wav = video.with_name(video.stem + "_audio.wav")
    if not wav.exists():
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000", str(wav)],
                       check=True)
    return wav


def load_wav(path: Path, start: float = 0.0, dur: float | None = None) -> tuple[np.ndarray, int]:
    """16bit PCM wav → (mono float32 [-1, 1], 샘플레이트)."""
    with wave.open(str(path)) as w:
        sr, ch = w.getframerate(), w.getnchannels()
        w.setpos(min(int(start * sr), w.getnframes()))
        n = w.getnframes() - w.tell() if dur is None else int(dur * sr)
        x = np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float32) / 32768
    if ch > 1:
        x = x[: len(x) // ch * ch].reshape(-1, ch).mean(1)
    return x, sr


def speech_mask(x: np.ndarray, sr: int, noise_db: float = NOISE_DB) -> np.ndarray:
    """칸마다 말소리가 있으면 True. mask[i]는 [i*HOP, (i+1)*HOP) 구간."""
    h = int(sr * HOP)
    n = len(x) // h
    rms = np.sqrt((x[: n * h].reshape(n, h) ** 2).mean(1) + 1e-12)
    return 20 * np.log10(rms) > noise_db


def speech_bounds(mask: np.ndarray, a: float, b: float) -> tuple[float, float] | None:
    """[a, b) 안에서 첫 말소리 시작과 마지막 말소리 끝. 말이 없으면 None."""
    i0 = int(a / HOP)
    idx = np.flatnonzero(mask[i0:int(b / HOP)])
    if not len(idx):
        return None
    return (i0 + idx[0]) * HOP, (i0 + idx[-1] + 1) * HOP


def inner_pauses(mask: np.ndarray, a: float, b: float, min_len: float) -> list[tuple[float, float]]:
    """[a, b) 안에서 말과 말 사이의 멈춤 [(시작, 끝), ...]. 가장자리 정적은 넣지 않는다."""
    i0 = int(a / HOP)
    idx = np.flatnonzero(mask[i0:int(b / HOP)])
    out = []
    for g in np.flatnonzero(np.diff(idx) > 1):
        s, e = idx[g] + 1, idx[g + 1]
        if (e - s) * HOP >= min_len - 1e-9:
            out.append(((i0 + s) * HOP, (i0 + e) * HOP))
    return out
