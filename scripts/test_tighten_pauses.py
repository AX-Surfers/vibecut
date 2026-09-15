#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy"]
# ///
"""tighten_pauses 자가 점검: uv run scripts/test_tighten_pauses.py"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from speech import HOP, inner_pauses  # noqa: E402
from tighten_pauses import learn_style, plan_pieces  # noqa: E402


def mask_of(total: float, *spans: tuple[float, float]) -> np.ndarray:
    m = np.zeros(int(round(total / HOP)), bool)
    for a, b in spans:
        m[int(round(a / HOP)):int(round(b / HOP))] = True
    return m


def demo():
    # 말 0.10~1.00 / 멈춤 0.8초 / 말 1.80~2.50 / 멈춤 0.4초 / 말 2.90~3.50, 클립은 0~3.8초
    m = mask_of(5, (0.10, 1.00), (1.80, 2.50), (2.90, 3.50))
    assert inner_pauses(m, 0, 3.8, 0.5) == [(1.0, 1.8)]
    # 가장자리: floor((0.10-0.06)*30)=1, ceil((3.50+0.04)*30)=107 / 0.8초 멈춤만 자름: 32~52 프레임
    assert plan_pieces(0, 3.8, m, 0.5, 0.04, 0.06, fps=30) == [(1, 32), (52, 107)]
    # 기준을 0.3으로 낮추면 0.4초 멈춤도 자른다 (77~85 프레임)
    assert plan_pieces(0, 3.8, m, 0.3, 0.04, 0.06, fps=30) == [(1, 32), (52, 77), (85, 107)]
    # 0.3초 멈춤은 여유(0.04+0.06)를 빼면 0.2초 미만만 남아 자르지 않는다
    m3 = mask_of(5, (0.10, 1.00), (1.30, 2.00))
    assert plan_pieces(0, 2.2, m3, 0.3, 0.04, 0.06, fps=30) == [(1, 62)]

    # 짧은 말("네" 0.2초) 뒤 긴 멈춤: 조각이 13프레임 미만이 되지 않게 멈춤을 덜 자르되, 멈춤은 여전히 자른다
    m = mask_of(4, (0.10, 0.30), (1.30, 3.00))
    pieces = plan_pieces(0, 3.2, m, 0.5, 0.04, 0.06, fps=30)
    assert pieces == [(1, 14), (37, 92)], pieces
    assert all(b - a >= 13 for a, b in pieces)

    # 말이 없는 클립은 그대로
    assert plan_pieces(10, 12, np.zeros(1000, bool), 0.5, 0.04, 0.06, fps=30) == [(300, 360)]

    # 손편집 스타일 학습: 말 앞 0.06 / 뒤 0.04 여유, 남긴 멈춤 최대 0.4초 → 0.5초 이상을 자른 것으로 본다
    m = mask_of(20, (1.06, 2.0), (2.4, 3.0), (5.06, 6.0), (6.2, 7.0), (9.06, 10.0))
    style = learn_style([(1.0, 3.04), (5.0, 7.04), (9.0, 10.04)], m)
    assert style == {"keep_before": 0.06, "keep_after": 0.04, "min_pause": 0.4}, style

    print("✓ tighten_pauses OK")


if __name__ == "__main__":
    demo()
