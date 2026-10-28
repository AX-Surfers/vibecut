#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""자막 조각 보정 자가 점검: uv run scripts/test_repair_parts.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from add_subtitles import repair_parts  # noqa: E402


def mk(text, a, b):
    return {"text": text, "start": a, "end": b}


def demo():
    got = [p["text"] for p in repair_parts([
        mk("아까처럼 제주도", 0, 1), mk("여행", 1, 1.5),             # 짧은 뒤 조각 → 앞에 붙임
        mk("관리하는 웹사이트 만들고", 1.5, 3), mk("싶어", 3, 3.4),
        mk("이런 식으로 사이트 기능 넣고", 3.4, 5),                    # 온전한 줄은 그대로
        mk("안녕하세요.", 5, 6), mk("네", 6, 6.3),                    # 끝난 문장 뒤는 붙이지 않음
        mk("해줄", 6.3, 7), mk("수가 있어요", 7, 8),                  # 서술부로 시작 → 앞에 붙임
        mk("내용이 아주 길어서 도저히 한 줄에 들어갈 수가 없을 것 같은", 8, 10), mk("싶어", 10, 10.3),  # 합치면 32자 초과 → 그대로
        mk("앞 줄", 11.5, 12), mk("한참 뒤", 13, 14),                  # 간격 > 0.6초 → 그대로
    ])]
    assert got == ["아까처럼 제주도 여행", "관리하는 웹사이트 만들고 싶어", "이런 식으로 사이트 기능 넣고",
                   "안녕하세요.", "네", "해줄 수가 있어요", "내용이 아주 길어서 도저히 한 줄에 들어갈 수가 없을 것 같은",
                   "싶어", "앞 줄", "한참 뒤"], got
    print("✓ repair_parts")


if __name__ == "__main__":
    demo()
