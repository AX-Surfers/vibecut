#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy"]
# ///
"""facecam 자가 점검: uv run scripts/test_facecam.py"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from facecam import (  # noqa: E402
    corner_transform,
    envelope,
    facecam_start_us,
    fit_sync,
    gating_ratio,
    slide_match,
    xcorr_lag,
)


def demo():
    # 실전 값: 1920x1080 캔버스에 세로 1080x1920 얼굴캠, 높이 35%
    scale, tx, ty, w, h = corner_transform(1920, 1080, 1080, 1920, 0.35, 30, "br")
    assert abs(scale - 0.35) < 1e-9 and round(w) == 213 and round(h) == 378
    assert abs(tx - 0.8580) < 1e-3 and abs(ty + 0.5944) < 1e-3        # 여백 30px (첫 배치 값)
    _, tx0, ty0, _, _ = corner_transform(1920, 1080, 1080, 1920, 0.35, 0, "br")
    assert abs(tx0 - (960 - w / 2) / 960) < 1e-9 and abs(ty0 + (540 - h / 2) / 540) < 1e-9   # 모서리에 딱 붙음
    _, txl, tyt, _, _ = corner_transform(1920, 1080, 1080, 1920, 0.35, 0, "tl")
    assert txl == -tx0 and tyt == -ty0

    # 대략 싱크: 얼굴캠이 화면녹화보다 1.40초 먼저 켜졌다 (얼굴캠 시각 = 화면녹화 시각 + 1.40)
    rng = np.random.default_rng(0)
    sr, total = 8000, 40.0
    env = np.repeat(rng.random(int(total * 5)) > 0.6, sr // 5)          # 0.2초 단위로 말/쉼
    screen = (rng.standard_normal(len(env)) * env * 0.3).astype(np.float32)
    lead = np.zeros(int(1.40 * sr), np.float32)
    face = np.concatenate([lead, screen]) + rng.standard_normal(len(lead) + len(screen)).astype(np.float32) * 1e-3
    lag, prominence = xcorr_lag(envelope(screen, sr, 0.01), envelope(face, sr, 0.01), 0.01)
    assert abs(-lag - 1.40) <= 0.01 and prominence > 1.5, (lag, prominence)
    # 창마다 양쪽으로 찾기: 얼굴캠 (t + 1.40 - 0.3)부터 뽑은 창 안에서 화면녹화 [t, t+10]이 0.3초 지점에 있어야 한다
    t = 12.0
    ref = envelope(face[int((t + 1.10) * sr): int((t + 11.70) * sr)], sr, 0.0025)
    k = slide_match(ref, envelope(screen[int(t * sr): int((t + 10) * sr)], sr, 0.0025))
    assert abs(1.10 + k * 0.0025 - 1.40) <= 0.0025, k

    # 시계 차이: 실전 측정값(1.430@60s … 1.340@2230s, -40ppm)은 직선으로 맞고, 끊김(0.2초 점프)은 걸러낸다
    real = [(60, 1.430), (257, 1.405), (455, 1.4075), (652, 1.405), (849, 1.4025), (1046, 1.3775),
            (1259, 1.375), (1456, 1.370), (1653, 1.375), (1835, 1.345), (2048, 1.3425), (2230, 1.340)]
    icpt, slope, worst = fit_sync(real)
    assert -45e-6 < slope < -35e-6 and worst < 0.015, (icpt, slope, worst)
    assert fit_sync(real[:6] + [(t, o + 0.2) for t, o in real[6:]])[2] > 0.025

    # 프레임 반올림: 본영상 조각(1000초, 2초)의 얼굴캠 시작 = 가운데(1001초) 오프셋, 가장 가까운 30fps 칸
    src = facecam_start_us(1_000_000_000, 2_000_000, 1.4267, -39.5e-6)
    expected = 1000 + 1.4267 - 39.5e-6 * 1001
    assert abs(src / 1e6 - expected) <= 1 / 60 + 1e-6 and round(src / 1e6 * 30, 6) == round(src / 1e6 * 30)

    # 노이즈 게이트 판정: 완전무음 칸이 많은 쪽을 가려낸다
    assert gating_ratio(screen, sr) > 0.3 and gating_ratio(face, sr) < 0.05

    print("✓ facecam OK")


if __name__ == "__main__":
    demo()
