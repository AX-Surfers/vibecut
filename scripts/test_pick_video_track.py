#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""편집 대상 트랙 선택 자체 점검.

실행: uv run scripts/test_pick_video_track.py

배경: 배경 이미지 1장이 타임라인 맨 위 트랙에 올라가 있는 상태에서
capcut_editor가 tracks[0]을 무조건 편집 대상으로 잡는 바람에, 원본 영상 대신
그 PNG를 38조각으로 잘라버린 사고가 있었다. 이 점검은 그 상황을 재현한다.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from capcut_editor import pick_video_track


def demo():
    # 사고 재현: 배경 이미지가 tracks[0], 원본 영상이 tracks[1]
    draft = {
        "materials": {"videos": [
            {"id": "img1", "path": "/x/background.png"},
            {"id": "v1", "path": "/x/talk.mov"},
        ]},
        "tracks": [
            {"type": "video", "segments": [{"material_id": "img1"}]},
            {"type": "video", "segments": [{"material_id": "v1"}] * 34},
            {"type": "text", "segments": [{}] * 130},
        ],
    }
    assert pick_video_track(draft) == 1, "이미지 트랙을 편집 대상으로 골랐다"

    # 단일 영상 트랙
    draft = {
        "materials": {"videos": [{"id": "v1", "path": "/x/a.mov"}]},
        "tracks": [{"type": "video", "segments": [{"material_id": "v1"}] * 41}],
    }
    assert pick_video_track(draft) == 0

    # 영상 트랙 없음
    draft = {
        "materials": {"videos": []},
        "tracks": [{"type": "text", "segments": [{}]}],
    }
    assert pick_video_track(draft) is None

    # 빈 세그먼트 트랙은 후보에서 제외
    draft = {
        "materials": {"videos": [{"id": "v1", "path": "/x/a.mov"}]},
        "tracks": [
            {"type": "video", "segments": []},
            {"type": "video", "segments": [{"material_id": "v1"}] * 5},
        ],
    }
    assert pick_video_track(draft) == 1

    # 동점(둘 다 영상)이면 세그먼트가 많은 쪽
    draft = {
        "materials": {"videos": [
            {"id": "a", "path": "/x/a.mov"},
            {"id": "b", "path": "/x/b.mov"},
        ]},
        "tracks": [
            {"type": "video", "segments": [{"material_id": "a"}] * 2},
            {"type": "video", "segments": [{"material_id": "b"}] * 9},
        ],
    }
    assert pick_video_track(draft) == 1

    print("OK: pick_video_track 5개 케이스 통과")


if __name__ == "__main__":
    demo()
