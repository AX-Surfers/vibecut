#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""멀티 타임라인 프로젝트 처리 자체 점검. 실행: uv run scripts/test_resolve_timeline.py

배경: 타임라인이 2개인 프로젝트에서 스크립트가 Timelines/ 첫 폴더만 잡아 엉뚱한
타임라인을 편집할 뻔했다. 이름으로 고르고, 지정이 없으면 멈추고, 메인이 아닌
타임라인은 루트 draft_info.json을 건드리지 않아야 한다.
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from capcut_editor import list_timelines, resolve_timeline, write_4_files


def make_project(root: Path, timelines: list[tuple[str, str]], main: str) -> Path:
    proj = root / "proj"
    (proj / "Timelines").mkdir(parents=True)
    (proj / "draft_info.json").write_text("{}")
    (proj / "draft_info.json.bak").write_text("{}")
    for tid, _ in timelines:
        d = proj / "Timelines" / tid
        d.mkdir()
        (d / "draft_info.json").write_text("{}")
        (d / "draft_info.json.bak").write_text("{}")
    (proj / "Timelines" / "project.json").write_text(json.dumps({
        "main_timeline_id": main,
        "timelines": [{"id": t, "name": n, "is_marked_delete": False} for t, n in timelines]}))
    return proj


def demo():
    with tempfile.TemporaryDirectory() as tmp:
        A, B = "AAAA-1111-A", "BBBB-2222-B"
        proj = make_project(Path(tmp), [(A, "타임라인 01"), (B, "타임라인 02")], main=A)

        tls, main = list_timelines(proj)
        assert [t["name"] for t in tls] == ["타임라인 01", "타임라인 02"] and main == A

        # 지정 없음 → 멈춤 (첫 폴더를 잡지 않는다)
        try:
            resolve_timeline(proj, None)
            raise AssertionError("타임라인 2개인데 멈추지 않음")
        except SystemExit as e:
            assert "타임라인 02" in str(e)

        assert resolve_timeline(proj, "타임라인 01") == (A, True)
        assert resolve_timeline(proj, "타임라인02") == (B, False)     # 공백 무시
        assert resolve_timeline(proj, "bbbb") == (B, False)          # id 앞부분, 메인 아님
        try:
            resolve_timeline(proj, "타임라인 03")
            raise AssertionError("없는 타임라인인데 통과")
        except SystemExit:
            pass

        # 메인이 아닌 타임라인 저장 → 루트 파일은 그대로
        write_4_files(proj, B, {"x": 1}, write_root=False)
        assert (proj / "draft_info.json").read_text() == "{}"
        assert json.loads((proj / "Timelines" / B / "draft_info.json").read_text()) == {"x": 1}
        write_4_files(proj, A, {"y": 2}, write_root=True)
        assert json.loads((proj / "draft_info.json").read_text()) == {"y": 2}

        # 타임라인 1개면 지정 없이 통과, 구형(Timelines 없음)이면 (None, True)
        single = make_project(Path(tmp) / "s", [(A, "Timeline 01")], main=A)
        assert resolve_timeline(single, None) == (A, True)
        legacy = Path(tmp) / "legacy"
        legacy.mkdir()
        assert resolve_timeline(legacy, None) == (None, True)
    print("OK: 타임라인 목록·선택·미지정 중단·비메인 루트 보호 모두 통과")


if __name__ == "__main__":
    demo()
