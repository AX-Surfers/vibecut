#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""자막 모양·레이어 자가 점검: uv run scripts/test_apply_subtitles.py"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from apply_subtitles import (  # noqa: E402
    SUBTITLE_SIZE,
    TRACK_NAME,
    build_text_track,
    find_user_edits,
    replace_subtitle_track,
    style_from_project,
)


def demo():
    parts = [{"start": 0.0, "end": 1.5, "text": "이번에 윈도우 Aside가"},
             {"start": 1.5, "end": 3.0, "text": "출시가 되었습니다"}]

    # 기본(box): 흰 글씨 + 반투명 검정 박스, 테두리·그림자 없음
    mats, track = build_text_track(parts, font_id=None)      # 폰트는 이 컴퓨터에 없을 수 있으니 생략
    assert len(mats) == len(track["segments"]) == 2 and track["name"] == TRACK_NAME
    m = mats[0]
    assert m["background_style"] == 1 and m["background_color"] == "#000000" and m["background_alpha"] == 0.6
    assert m["border_width"] == 0.0 and m["has_shadow"] is False
    assert m["font_size"] == SUBTITLE_SIZE and m["text_size"] == round(SUBTITLE_SIZE * 5)
    st = json.loads(m["content"])["styles"][0]
    assert st["size"] == SUBTITLE_SIZE and st["fill"]["content"]["solid"]["color"] == [1, 1, 1]
    assert "strokes" not in st and "shadows" not in st
    assert json.loads(m["content"])["text"] == parts[0]["text"]
    assert track["segments"][0]["target_timerange"]["start"] == 0

    # outline: 검정 테두리 있고 박스 없음
    mats_o, _ = build_text_track(parts, style="outline", size=6.0, font_id=None)
    st_o = json.loads(mats_o[0]["content"])["styles"][0]
    assert st_o["strokes"][0]["width"] == 0.15 and mats_o[0]["background_style"] == 0
    assert mats_o[0]["font_size"] == 6.0 and mats_o[0]["text_size"] == 30

    # 레이어: 얼굴캠 오버레이(track_render_index 2)보다 자막이 위, 트랙 배열도 맨 뒤
    draft = {"tracks": [
        {"type": "video", "segments": [{"material_id": "v", "track_render_index": 0}]},
        {"type": "text", "name": TRACK_NAME, "segments": [{"material_id": "old"}]},
        {"type": "video", "segments": [{"material_id": "pip", "track_render_index": 2}]},
    ], "materials": {"texts": [{"id": "old"}, {"id": "keep"}]}}
    log = replace_subtitle_track(draft, mats, track)
    assert draft["tracks"][-1] is track and len(draft["tracks"]) == 3
    assert all(s["track_render_index"] == 3 for s in track["segments"]), track["segments"]
    assert {m["id"] for m in draft["materials"]["texts"]} == {"keep"} | {m["id"] for m in mats}
    assert any("레이어" in line for line in log)

    # 기존 프로젝트에서 모양 읽어오기 (--style-from)
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        proj = Path(tmp) / "proj"
        (proj / "Timelines" / "AAA").mkdir(parents=True)
        mat = {"id": "m1", "background_style": 1, "background_alpha": 0.45, "border_width": 0.0, "text_size": 42,
               "content": json.dumps({"styles": [{"size": 7.5, "font": {"path": "/tmp/f.ttf", "id": "999"}}], "text": "x"})}
        draft = {"materials": {"texts": [mat]},
                 "tracks": [{"type": "text", "name": TRACK_NAME,
                             "segments": [{"material_id": "m1", "target_timerange": {"start": 0, "duration": 1}}]}]}
        for f in (proj / "draft_info.json", proj / "Timelines/AAA/draft_info.json"):
            f.write_text(json.dumps(draft), encoding="utf-8")
        look = style_from_project(proj)
        assert look == {"style": "box", "size": 7.5, "font_path": "/tmp/f.ttf", "font_id": "999",
                        "box_alpha": 0.45, "border_width": 0.0, "text_size": 42}, look
        mats_s, _ = build_text_track(parts, **look)
        st_s = json.loads(mats_s[0]["content"])["styles"][0]
        assert st_s["size"] == 7.5 and st_s["font"] == {"path": "/tmp/f.ttf", "id": "999"}
        assert mats_s[0]["background_alpha"] == 0.45 and mats_s[0]["text_size"] == 42

    # CapCut에서 직접 고친 문구 찾기: 같은 자리 문구가 다르면 손으로 고친 것
    old = [{"start": 0.0, "end": 1.5, "text": "이번에 윈도우 Aside가"},      # 그대로
           {"start": 1.5, "end": 3.0, "text": "출시가 됐습니다"}]            # 사용자가 고침
    edits = find_user_edits(parts, old)
    assert [(p_["text"], t) for p_, t in edits] == [("출시가 되었습니다", "출시가 됐습니다")], edits
    assert find_user_edits(parts, []) == []                                   # 기존 자막이 없으면 비교 안 함
    assert find_user_edits(parts, [{"start": 9.0, "end": 9.5, "text": "딴 데"}]) == []   # 시간이 안 겹치면 무시

    print("✓ apply_subtitles 자막 모양·레이어 OK")


if __name__ == "__main__":
    demo()
