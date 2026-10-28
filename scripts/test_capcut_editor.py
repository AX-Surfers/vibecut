#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""capcut_editor 저장·복제 자가 점검: uv run scripts/test_capcut_editor.py"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from capcut_editor import clone_video_segment, write_4_files  # noqa: E402


def demo():
    # 세그먼트 복제: 소재 7종은 새로 만들고, 템플릿에 붙은 크로마키는 복제해서 유지한다
    tmpl_seg = {"id": "s0", "material_id": "v0", "extra_material_refs": ["spd0", "chr0"],
                "target_timerange": {"start": 0, "duration": 10}, "source_timerange": {"start": 0, "duration": 10},
                "clip": {"scale": {"x": 0.4, "y": 0.4}}, "volume": 1.0, "render_index": 1, "track_render_index": 2}
    draft_materials = {"videos": [{"id": "v0", "path": "/tmp/a.mp4"}],
                       "speeds": [{"id": "spd0", "type": "speed"}],
                       "chromas": [{"id": "chr0", "type": "chroma", "color": "#2eb245ff", "intensity_value": 0.2}]}
    added = {}
    seg = clone_video_segment(tmpl_seg, draft_materials["videos"][0], 5_000_000, 2_000_000, 7_000_000,
                              added, draft_materials=draft_materials)
    assert len(added["chromas"]) == 1 and added["chromas"][0]["color"] == "#2eb245ff"
    assert added["chromas"][0]["id"] != "chr0"                       # 새 id
    assert added["chromas"][0]["id"] in seg["extra_material_refs"]    # 새 세그먼트가 참조
    assert "chr0" not in seg["extra_material_refs"] and "spd0" not in seg["extra_material_refs"]
    assert len(seg["extra_material_refs"]) == 7                       # 기본 6종 + 크로마
    assert seg["clip"]["scale"]["x"] == 0.4 and seg["volume"] == 1.0 and seg["render_index"] == 1
    assert seg["source_timerange"] == {"start": 5_000_000, "duration": 2_000_000}
    assert seg["target_timerange"] == {"start": 7_000_000, "duration": 2_000_000}
    # draft_materials를 안 주면 예전처럼 기본 6종만 (크로마 없음)
    assert len(clone_video_segment(tmpl_seg, draft_materials["videos"][0], 0, 1, 0, {})["extra_material_refs"]) == 6

    # 저장할 때 트랙 세그먼트를 시간순으로 정렬한다 (메인 트랙이 배열 순서대로 배치되므로)
    with tempfile.TemporaryDirectory() as tmp:
        proj = Path(tmp) / "proj"
        (proj / "Timelines" / "TL").mkdir(parents=True)
        draft = {"duration": 30, "tracks": [
            {"type": "video", "segments": [{"target_timerange": {"start": 20, "duration": 10}, "id": "b"},
                                           {"target_timerange": {"start": 0, "duration": 10}, "id": "a"}]},
            {"type": "text", "segments": [{"target_timerange": {"start": 5, "duration": 1}, "id": "t2"},
                                          {"target_timerange": {"start": 1, "duration": 1}, "id": "t1"}]},
        ]}
        write_4_files(proj, "TL", draft, write_root=True)
        saved = json.loads((proj / "Timelines/TL/draft_info.json").read_text(encoding="utf-8"))
        assert [s["id"] for s in saved["tracks"][0]["segments"]] == ["a", "b"]
        assert [s["id"] for s in saved["tracks"][1]["segments"]] == ["t1", "t2"]
        assert json.loads((proj / "draft_info.json").read_text(encoding="utf-8")) == saved

    print("✓ capcut_editor 복제(효과 유지)·저장 정렬 OK")


if __name__ == "__main__":
    demo()
