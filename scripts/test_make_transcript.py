#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""정적 표시 transcript 자체 점검. 실행: uv run scripts/test_make_transcript.py

배경: Whisper는 말이 멈춘 시간을 앞 단어 길이에 흡수시켜 대본만 읽으면 정적이
안 보인다. 실전 데이터("한" 38.88~39.54s, 정적 38.96~43.07s, "달" 43.2s~)를 그대로
재현해 ⏸ 표시가 그 자리에 들어가는지 확인한다.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from make_transcript import build_transcript, split_words_by_silence


def demo():
    seg = {"start": 38.88, "end": 45.7, "text": "한 달 20시간의 과정을 마치고 나면", "words": [
        {"word": " 한", "start": 38.88, "end": 39.54},
        {"word": " 달", "start": 43.2, "end": 43.44},
        {"word": " 20시간의", "start": 43.44, "end": 44.5},
        {"word": " 나면", "start": 45.3, "end": 45.7},
    ]}
    silences = [(30.0, 36.0), (38.96, 43.07), (43.5, 43.9), (45.72, 49.47)]

    groups = split_words_by_silence(seg["words"], silences, 0.7)
    assert [[w["word"].strip() for w in ws] for ws, _ in groups] == [["한"], ["달", "20시간의", "나면"]], groups
    assert groups[0][1] == (38.96, 43.07) and groups[1][1] is None
    # 0.4초짜리 정적(43.5~43.9)은 min_silence 미만이라 표시하지 않는다

    text = build_transcript([seg, {"start": 49.5, "end": 50.0, "text": "다음", "words": [
        {"word": " 다음", "start": 49.5, "end": 50.0}]}], silences)
    lines = text.splitlines()
    assert lines[0].endswith("] 한") and "(38.9~39.0s)" in lines[0], lines[0]
    assert "⏸ 정적 4.1초 (39.0~43.1s)" in lines[1], lines[1]
    assert lines[2].startswith("[00:43 (43.2~45.7s)] 달"), lines[2]
    assert "⏸ 정적 3.8초" in lines[3], lines[3]        # 문장 사이 침묵도 표시
    assert lines[4].endswith("] 다음")
    # 문장 사이 공백에 소리가 있으면(정적 아님) Whisper가 놓친 말일 수 있다고 표시
    noisy = build_transcript([seg, {"start": 70.0, "end": 71.0, "text": "끝", "words": [
        {"word": " 끝", "start": 70.0, "end": 71.0}]}], silences).splitlines()
    assert "🔊 인식 안 된 소리 24.3초" in noisy[3], noisy[3]

    # 정적이 첫 단어 앞에서 시작하면(문장 사이 침묵) 문장 안에서 쪼개지 않는다
    assert len(split_words_by_silence(seg["words"], [(38.0, 39.5)], 0.7)) == 1
    # 오디오가 없을 때의 단어 간격 폴백도 같은 자리를 잡는다
    assert len(build_transcript([seg], None).splitlines()) == 3
    print("OK: 정적 흡수 단어 분리, 짧은 정적 무시, 문장 사이 침묵 표시, 폴백 모두 통과")


if __name__ == "__main__":
    demo()
