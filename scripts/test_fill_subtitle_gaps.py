#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy"]
# ///
"""fill_subtitle_gaps 자가 점검: uv run scripts/test_fill_subtitle_gaps.py"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from fill_subtitle_gaps import chunk, find_gaps, join_words, words_in  # noqa: E402
from speech import HOP  # noqa: E402


def mask_of(total: float, *spans: tuple[float, float]) -> np.ndarray:
    m = np.zeros(int(round(total / HOP)), bool)
    for a, b in spans:
        m[int(round(a / HOP)):int(round(b / HOP))] = True
    return m


def demo():
    # 원본 0~10초 중 말소리 1~3초, 5~9초. 클립은 원본 1~9초를 타임라인 0~8초에 올림
    mask = mask_of(12, (1.0, 3.0), (5.0, 9.0))
    clips = [(0.0, 8.0, 1.0)]
    # 기존 자막이 타임라인 0~2초(=원본 1~3초)만 덮고 있으면, 원본 5~9초 자리가 빈 곳
    gaps = find_gaps(clips, [(0.0, 2.0)], mask, min_gap=0.4)
    assert len(gaps) == 1, gaps
    ta, tb, sa = gaps[0]
    assert abs(ta - 4.0) < 0.05 and abs(tb - 8.0) < 0.05 and abs(sa - 5.0) < 0.05, gaps
    # 자막이 전부 덮고 있으면 빈 곳 없음
    assert find_gaps(clips, [(0.0, 8.0)], mask, min_gap=0.4) == []
    # 짧은 구간은 무시 (min_gap)
    assert find_gaps(clips, [(0.0, 2.0), (4.3, 8.0)], mask, min_gap=0.5) == []

    words = [{"word": " 안녕", "start": 5.0, "end": 5.4}, {"word": " 하세요", "start": 5.4, "end": 6.0},
             {"word": " 딴말", "start": 20.0, "end": 20.5}]
    assert [w["word"].strip() for w in words_in(words, 5.0, 9.0)] == ["안녕", "하세요"]
    # 글자 수 제한 안에서 단어 단위로 묶는다
    long_words = [{"word": " 가나다라마", "start": i, "end": i + 0.5} for i in range(4)]
    groups = chunk(long_words, 12)
    assert all(len(" ".join(w["word"].strip() for w in g)) <= 12 for g in groups) and sum(len(g) for g in groups) == 4

    # 조사는 앞말에 붙이고, 끝 문장부호는 뗀다 (다른 자막에 마침표가 없으므로)
    assert join_words([{"word": " MCP"}, {"word": " 를"}, {"word": " 제공해서"}]) == "MCP를 제공해서"
    assert join_words([{"word": " 요즘"}, {"word": " 핫하거든요."}]) == "요즘 핫하거든요"

    print("✓ fill_subtitle_gaps 빈 곳 찾기·묶기 OK")


if __name__ == "__main__":
    demo()
