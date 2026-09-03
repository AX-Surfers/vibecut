#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""자막 경계 == 컷 경계 자체 점검.

실행: uv run scripts/test_subtitles_from_cuts.py

배경: 편집 오디오를 재전사해 자막을 만들자 자막이 컷을 가로지르고 같은 문장이
두 번 들어가는 문제가 있었다. 이 점검은 컷 매핑 방식이 그 두 가지를 구조적으로
막는지, 그리고 NG 제거·패딩·문장 중간 수동 컷까지 올바르게 다루는지 확인한다.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from subtitles_from_cuts import build_units, check_alignment


def words(*pairs):
    return [{"word": f" w{s}", "start": s, "end": e} for s, e in pairs]


def demo():
    segments = [
        {"start": 10.0, "end": 12.0, "text": "A", "words": words((10.0, 10.5), (10.6, 11.2), (11.3, 12.0))},
        {"start": 12.5, "end": 14.0, "text": "B", "words": words((12.5, 13.0), (13.1, 14.0))},
        {"start": 20.0, "end": 22.0, "text": "NG", "words": words((20.0, 21.0), (21.0, 22.0))},
        {"start": 30.0, "end": 31.0, "text": "D", "words": words((30.0, 30.4), (30.5, 31.0))},
        {"start": 40.0, "end": 44.0, "text": "E", "words": words((40.0, 40.9), (41.0, 41.9), (42.0, 42.9), (43.0, 43.9))},
    ]
    # 컷: A+B 병합(시작패딩 0.12, 끝패딩 0.2) / NG 세그먼트는 컷 없음 / D /
    #     E는 사용자가 문장 중간(42.0)을 손으로 잘라 두 컷으로 나눔
    cuts = [
        {"src_s": 9.88, "src_e": 14.2, "tgt_s": 0.0, "tgt_e": 4.32},
        {"src_s": 29.88, "src_e": 31.2, "tgt_s": 4.32, "tgt_e": 5.64},
        {"src_s": 40.0, "src_e": 42.0, "tgt_s": 5.64, "tgt_e": 7.64},
        {"src_s": 42.0, "src_e": 44.0, "tgt_s": 7.64, "tgt_e": 9.64},
    ]

    units = build_units(segments, cuts)
    assert check_alignment(units, cuts) == [], check_alignment(units, cuts)

    texts = [u["text"] for u in units]
    # NG 세그먼트는 사라진다
    assert not any("w20" in t for t in texts), texts
    # 중복 없음
    assert len(texts) == len(set(zip(texts, [u["clip"] for u in units])))
    # A+B 병합 컷: 첫 단위 시작 = 컷 시작, 마지막 단위 끝 = 컷 끝
    a, b = [u for u in units if u["clip"] == 0]
    assert a["start"] == 0.0 and b["end"] == 4.32
    assert a["end"] <= b["start"]
    # 패딩 영역(원본 9.88~10.0) 때문에 첫 단어가 컷 밖으로 나가지 않는다
    assert a["words"][0]["start"] >= 0.0
    # 문장 중간 수동 컷: 단어가 양쪽 컷으로 정확히 나뉘고 경계는 7.64
    e1, e2 = [u for u in units if u["clip"] in (2, 3)]
    assert [w["word"] for w in e1["words"]] == [" w40.0", " w41.0"]
    assert [w["word"] for w in e2["words"]] == [" w42.0", " w43.0"]
    assert e1["end"] == 7.64 and e2["start"] == 7.64

    # 실전 사례 1: Whisper가 정적을 흡수시킨 단어 — "한" 38.88~39.54, 컷은 정적 시작 39.06에서 끝
    # 실전 사례 2: 시작 시각이 정적 안에 일찍 찍힌 단어 — "중이니까" 59.3~60.4, 다음 컷은 60.01부터
    real_segs = [
        {"start": 36.4, "end": 38.9, "text": "봐드립니다", "words": words((36.4, 37.5), (37.5, 38.9))},
        {"start": 38.88, "end": 45.7, "text": "한 달", "words": [
            {"word": " 한", "start": 38.88, "end": 39.54}, {"word": " 달", "start": 43.2, "end": 45.7}]},
        {"start": 56.4, "end": 57.9, "text": "신청", "words": words((56.4, 57.9))},
        {"start": 59.3, "end": 62.9, "text": "중이니까", "words": [
            {"word": " 중이니까", "start": 59.3, "end": 60.4}, {"word": " 보세요", "start": 60.4, "end": 62.9}]},
    ]
    real_cuts = [
        {"src_s": 36.28, "src_e": 39.06, "tgt_s": 0.0, "tgt_e": 2.78},
        {"src_s": 42.97, "src_e": 45.9, "tgt_s": 2.78, "tgt_e": 5.71},
        {"src_s": 56.27, "src_e": 58.33, "tgt_s": 5.71, "tgt_e": 7.77},
        {"src_s": 60.01, "src_e": 63.05, "tgt_s": 7.77, "tgt_e": 10.81},
    ]
    ru = build_units(real_segs, real_cuts)
    assert check_alignment(ru, real_cuts) == []
    rtexts = [u["text"] for u in ru]
    assert "한" in "".join(rtexts) and "중이니까" in "".join(rtexts), rtexts   # 글자 유실 없음
    assert rtexts[0].endswith("한"), rtexts        # 0.18초짜리 "한"은 같은 컷의 앞 자막에 합쳐짐
    assert ru[-1]["text"].startswith("중이니까") and ru[-1]["clip"] == 3, rtexts

    # 위반을 실제로 잡는지 — 경계를 일부러 넘긴 단위
    bad = [dict(units[0], end=cuts[0]["tgt_e"] + 1.0)]
    assert check_alignment(bad, cuts), "경계 초과를 못 잡음"

    print(f"OK: {len(units)}개 단위, 컷 경계 위반 0건, NG 제거·병합·패딩·수동 중간컷·정적 흡수 단어 모두 통과")


if __name__ == "__main__":
    demo()
