#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""
Vibecut — 기존 CapCut 프로젝트에 자막 트랙 추가 (다른 트랙은 그대로)

예전에는 이 코드가 SKILL.md 안에 붙여넣기로 있어서 매번 다시 타이핑됐고,
`draft["tracks"] = [tracks[0], 새 자막]`으로 써서 배경음악·이미지 오버레이·
사용자가 직접 넣은 텍스트까지 전부 지워 버렸다. 이 스크립트는:
  - 비디오/오디오/기타 트랙을 전부 보존한다
  - vibecut이 만든 자막 트랙(name == "vibecut")만 교체한다. 그 밖의 텍스트 트랙은
    건드리지 않고 목록만 보여준다 (예전 버전이 만든 이름 없는 자막 트랙을 바꾸려면
    --replace-track N 으로 명시)
  - 4개 파일(루트 + Timelines/<uuid>) 동시 저장, 자동 백업, .locked 삭제

사용:
  uv run apply_subtitles.py --project <CapCut 프로젝트> [--timeline "타임라인 02"] \\
      --input <stem>_subtitle_input.json --splits <stem>_subtitle_splits.json
"""
import argparse
import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from add_subtitles import (  # noqa: E402
    DEFAULT_TEXT_MATERIAL,
    DEFAULT_TEXT_SEGMENT,
    new_id,
    remove_overlaps,
    repair_parts,
    snap_to_frame,
    split_by_splits,
)
from capcut_editor import draft_path_for, resolve_timeline, write_4_files  # noqa: E402

TRACK_NAME = "vibecut"
SUBTITLE_Y = -0.7407407407407407   # 화면 하단 위치 (CapCut 좌표)

# 기본 자막 모양 — 유튜브에서 흔한 "흰 글씨 + 반투명 검정 박스" (사용자 요청으로 기본값이 됨).
# 얇은 테두리만 있던 예전 기본값은 PIP(얼굴캠)나 밝은 화면 위에서 잘 안 읽혔다.
SUBTITLE_STYLE = "box"          # box | outline
SUBTITLE_SIZE = 6.0
SUBTITLE_BOX_ALPHA = 0.6
SUBTITLE_BORDER_WIDTH = 0.15    # outline 스타일에서만 사용
# 폰트는 기본적으로 건드리지 않는다 (프로젝트/템플릿 폰트 유지).
# 실전에서 Cafe24 Ohsquare(id 7528305055972199681)로 바꿔 넣었더니 CapCut에서 다시 열었을 때
# 폰트·크기가 템플릿 값으로 되돌아가 있었다. 특정 폰트를 강제하려면 --font-id로 지정한다.
SUBTITLE_FONT_ID: str | None = None
TEXT_SIZE_PER_UNIT = 5          # CapCut 메타데이터 text_size = size × 5 (기본 size 6 ↔ text_size 30)


def find_capcut_font(font_id: str) -> str | None:
    """CapCut이 받아 둔 폰트 파일 경로. 이 컴퓨터에 없으면 None (템플릿 폰트를 그대로 쓴다)."""
    roots = [Path.home() / "Library/Containers/com.lemon.lvoverseas/Data/Movies/CapCut/User Data/Cache/effect",
             Path.home() / "Movies/CapCut/User Data/Cache/effect"]
    for root in roots:
        for f in sorted((root / font_id).glob("*/*.ttf")) + sorted((root / font_id).glob("*/*.otf")):
            return str(f)
    return None


def style_from_project(project: Path, timeline: str | None = None) -> dict:
    """기존 프로젝트의 vibecut 자막에서 모양을 읽어 온다 ("지금 쓰는 자막 그대로" 용)."""
    tl, _ = resolve_timeline(Path(project).expanduser(), timeline)
    d = json.loads(draft_path_for(Path(project).expanduser(), tl).read_text(encoding="utf-8"))
    tm = {m["id"]: m for m in d["materials"].get("texts", [])}
    track = next((t for t in d["tracks"] if t.get("type") == "text" and t.get("name") == TRACK_NAME), None)
    if not track or not track.get("segments"):
        raise SystemExit(f"❌ {project}에 vibecut 자막 트랙이 없습니다 — 모양을 읽어올 수 없습니다")
    mat = tm[sorted(track["segments"], key=lambda s: s["target_timerange"]["start"])[0]["material_id"]]
    st = json.loads(mat["content"])["styles"][0]
    return {"style": "box" if mat.get("background_style") == 1 else "outline",
            "size": st.get("size", SUBTITLE_SIZE),
            "font_path": st.get("font", {}).get("path") or None,
            "font_id": st.get("font", {}).get("id") or None,
            "box_alpha": mat.get("background_alpha", SUBTITLE_BOX_ALPHA),
            "border_width": mat.get("border_width", SUBTITLE_BORDER_WIDTH),
            # CapCut UI에 보이는 크기 값. 렌더링은 content.styles[].size가 하지만, 둘이 어긋나 있으면
            # CapCut에서 자막을 손대는 순간 UI 값 쪽으로 맞춰지므로 같이 가져온다.
            "text_size": mat.get("text_size")}


def apply_subtitle_style(mat: dict, style: str = SUBTITLE_STYLE, size: float = SUBTITLE_SIZE,
                         font_id: str | None = SUBTITLE_FONT_ID, box_alpha: float = SUBTITLE_BOX_ALPHA,
                         border_width: float = SUBTITLE_BORDER_WIDTH, font_path: str | None = None,
                         text_size: int | None = None) -> None:
    """자막 소재에 모양을 입힌다 (content.styles = 실제 렌더링, 나머지 필드 = CapCut UI 값).

    box     — 흰 글씨 + 반투명 검정 박스. 테두리·그림자는 박스와 겹쳐 지저분해지므로 끈다.
    outline — 흰 글씨 + 검정 테두리 (예전 기본값).
    """
    font_path = font_path or (find_capcut_font(font_id) if font_id else None)
    c = json.loads(mat["content"])
    for st in c.get("styles", []):
        st["size"] = size
        st["fill"] = {"content": {"solid": {"color": [1, 1, 1]}, "render_type": "solid"}}
        if font_path:
            st["font"] = {"path": font_path, "id": font_id}
        if style == "box":
            st.pop("strokes", None)
            st.pop("shadows", None)
            st["useLetterColor"] = True
        else:
            st["strokes"] = [{"content": {"render_type": "solid", "solid": {"alpha": 1.0, "color": [0.0, 0.0, 0.0]}},
                              "width": border_width}]
    mat["content"] = json.dumps(c, ensure_ascii=False)
    mat["font_size"] = size
    mat["text_size"] = text_size if text_size is not None else round(size * TEXT_SIZE_PER_UNIT)
    if font_path:
        mat["font_path"] = font_path
        mat["font_resource_id"] = font_id
    if style == "box":
        mat.update({"border_width": 0.0, "border_alpha": 1.0, "has_shadow": False,
                    "background_style": 1, "background_color": "#000000", "background_alpha": box_alpha,
                    "background_round_radius": 0.0, "background_width": 0.28, "background_height": 0.28,
                    "background_vertical_offset": 0.0, "background_horizontal_offset": 0.0})
    else:
        mat.update({"border_width": border_width, "border_color": "#000000", "border_alpha": 1.0,
                    "border_mode": 0, "has_shadow": True, "background_style": 0})


def existing_subtitles(draft: dict) -> list[dict]:
    """지금 프로젝트의 vibecut 자막 [{start, end, text}] (초)."""
    tm = {m["id"]: m for m in draft["materials"].get("texts", [])}
    track = next((t for t in draft["tracks"] if t.get("type") == "text" and t.get("name") == TRACK_NAME), None)
    if not track:
        return []
    out = []
    for seg in track["segments"]:
        a = seg["target_timerange"]["start"] / 1e6
        b = a + seg["target_timerange"]["duration"] / 1e6
        mat = tm.get(seg.get("material_id"))
        if mat:
            out.append({"start": a, "end": b, "text": json.loads(mat["content"])["text"]})
    return sorted(out, key=lambda s: s["start"])


def find_user_edits(parts: list[dict], old: list[dict]) -> list[tuple[dict, str]]:
    """새로 넣을 자막과 시간이 겹치는 기존 자막 중 문구가 다른 것 [(새 파트, 기존 문구)].

    사용자가 CapCut에서 직접 고친 문구("에스크"→"Ask" 등)는 자막을 다시 넣으면 사라진다.
    같은 자리(겹침 50% 이상)의 문구가 다르면 손으로 고친 것으로 본다.
    """
    edits = []
    for part in parts:
        best, best_ov = None, 0.0
        for o in old:
            ov = min(part["end"], o["end"]) - max(part["start"], o["start"])
            if ov > best_ov:
                best, best_ov = o, ov
        if best is None:
            continue
        span = min(part["end"] - part["start"], best["end"] - best["start"])
        if best_ov >= span * 0.5 and best["text"].strip() != part["text"].strip():
            edits.append((part, best["text"]))
    return edits


def build_text_track(parts: list[dict], style: str = SUBTITLE_STYLE, size: float = SUBTITLE_SIZE,
                     font_id: str | None = SUBTITLE_FONT_ID, box_alpha: float = SUBTITLE_BOX_ALPHA,
                     font_path: str | None = None, border_width: float = SUBTITLE_BORDER_WIDTH,
                     text_size: int | None = None) -> tuple[list[dict], dict]:
    """자막 파트 → (text materials, text track)."""
    materials, segments = [], []
    for render_idx, part in enumerate(parts):
        start_us, end_us = snap_to_frame(part["start"]), snap_to_frame(part["end"])
        if end_us <= start_us:
            continue
        mat = copy.deepcopy(DEFAULT_TEXT_MATERIAL)
        mat["id"] = new_id()
        content = json.loads(mat["content"])
        content["text"] = part["text"]
        for st in content.get("styles", []):        # 루프 변수를 style로 쓰면 인자 style을 덮어써 모양이 안 먹는다
            st["range"] = [0, len(part["text"])]
        mat["content"] = json.dumps(content, ensure_ascii=False)
        apply_subtitle_style(mat, style=style, size=size, font_id=font_id, box_alpha=box_alpha,
                             border_width=border_width, font_path=font_path, text_size=text_size)
        materials.append(mat)

        seg = copy.deepcopy(DEFAULT_TEXT_SEGMENT)
        seg.update({
            "id": new_id(), "material_id": mat["id"],
            "extra_material_refs": [], "source_timerange": None,
            "target_timerange": {"start": start_us, "duration": end_us - start_us},
            "render_index": 14000 + render_idx,
        })
        seg.setdefault("clip", {}).setdefault("transform", {})["y"] = SUBTITLE_Y
        segments.append(seg)
    track = {"id": new_id(), "attribute": 0, "flag": 0, "is_default_name": False,
             "name": TRACK_NAME, "type": "text", "segments": segments}
    return materials, track


def replace_subtitle_track(draft: dict, materials: list[dict], track: dict,
                           replace_idx: int | None = None) -> list[str]:
    """vibecut 자막 트랙만 교체하고 나머지 트랙은 보존. 반환: 로그 줄들."""
    log = []
    keep, removed_mat_ids = [], set()
    for i, t in enumerate(draft["tracks"]):
        is_ours = t.get("type") == "text" and (t.get("name") == TRACK_NAME or i == replace_idx)
        if is_ours:
            removed_mat_ids |= {s.get("material_id") for s in t.get("segments", [])}
            log.append(f"  ↻ tracks[{i}] text '{t.get('name') or '(이름 없음)'}' {len(t.get('segments', []))}개 → 교체")
        else:
            keep.append(t)
            if t.get("type") == "text":
                log.append(f"  · tracks[{i}] text '{t.get('name') or '(이름 없음)'}' "
                           f"{len(t.get('segments', []))}개 보존 (교체하려면 --replace-track {i})")
            else:
                log.append(f"  · tracks[{i}] {t.get('type')} {len(t.get('segments', []))}개 보존")
    # 자막은 얼굴캠(PIP) 같은 영상 오버레이보다 위에 그려야 가려지지 않는다.
    # CapCut은 트랙 배열 순서와 track_render_index로 층을 정하므로 둘 다 맨 위로 둔다.
    top = max((s.get("track_render_index", 0) for t in keep for s in t.get("segments", [])), default=0) + 1
    for s in track["segments"]:
        s["track_render_index"] = top
    draft["tracks"] = keep + [track]
    texts = [m for m in draft["materials"].get("texts", []) if m.get("id") not in removed_mat_ids]
    draft["materials"]["texts"] = texts + materials
    log.append(f"  ▲ 자막 레이어 {top} (영상 오버레이보다 위)")
    return log


def main():
    ap = argparse.ArgumentParser(description="CapCut 프로젝트에 자막 트랙 추가 (다른 트랙 보존)")
    ap.add_argument("--project", required=True)
    ap.add_argument("--timeline", default=None, help="타임라인이 여러 개면 이름 또는 id 앞부분")
    ap.add_argument("--input", help="subtitle_input.json (타임라인 기준 시각)")
    ap.add_argument("--splits", help="subtitle_splits.json (텍스트 조각 배열, 1:1)")
    ap.add_argument("--repair-existing", action="store_true",
                    help="전사·분할을 다시 하지 않고, 지금 프로젝트의 vibecut 자막에서 어색하게 쪼개진 조각만 "
                         "이웃과 이어 붙인다 (CapCut에서 고친 문구·시각은 그대로). --input/--splits 불필요")
    ap.add_argument("--replace-track", type=int, default=None,
                    help="이 인덱스의 텍스트 트랙도 교체 (예전 버전이 만든 이름 없는 자막 트랙)")
    ap.add_argument("--style", choices=["box", "outline"], default=SUBTITLE_STYLE,
                    help=f"자막 모양 (기본 {SUBTITLE_STYLE}: 흰 글씨 + 반투명 검정 박스)")
    ap.add_argument("--size", type=float, default=SUBTITLE_SIZE, help=f"글자 크기 (기본 {SUBTITLE_SIZE})")
    ap.add_argument("--box-alpha", type=float, default=SUBTITLE_BOX_ALPHA, help="박스 진하기 0~1 (기본 0.6)")
    ap.add_argument("--font-id", default=SUBTITLE_FONT_ID,
                    help="CapCut 폰트 id (없는 컴퓨터면 템플릿 폰트 유지). 기본은 폰트 안 바꿈")
    ap.add_argument("--style-from", default=None, metavar="PROJECT",
                    help="이 프로젝트의 vibecut 자막과 같은 모양으로 넣는다 (지금 쓰는 자막 그대로)")
    ap.add_argument("--keep-edits", action="store_true",
                    help="CapCut에서 직접 고친 자막 문구를 그대로 유지 (기본은 알려주기만 함)")
    ap.add_argument("--no-check", action="store_true", help="CapCut 실행 여부 확인 건너뜀")
    args = ap.parse_args()

    if not args.no_check:
        from _platform import check_capcut_not_running
        check_capcut_not_running()

    proj = Path(args.project).expanduser()
    timeline_uuid, write_root = resolve_timeline(proj, args.timeline)
    draft = json.loads(draft_path_for(proj, timeline_uuid).read_text(encoding="utf-8"))

    if args.repair_existing:
        old = existing_subtitles(draft)
        if not old:
            raise SystemExit("❌ 이 프로젝트에 vibecut 자막 트랙이 없습니다")
        fixed = repair_parts(old)
        print(f"  🔧 자막 {len(old)}개 → {len(fixed)}개 (조각 {len(old) - len(fixed)}개를 이웃과 이었습니다)")
        look = style_from_project(proj, args.timeline)
        materials, track = build_text_track(fixed, **look)
        for line in replace_subtitle_track(draft, materials, track, args.replace_track):
            print(line)
        print("\n💾 파일 저장 중...")
        write_4_files(proj, timeline_uuid, draft, write_root=write_root)
        print(f"\n✓ 자막 {len(track['segments'])}개 (트랙 '{TRACK_NAME}'), 다른 트랙 {len(draft['tracks']) - 1}개 보존")
        return
    if not (args.input and args.splits):
        raise SystemExit("❌ --input과 --splits가 필요합니다 (또는 --repair-existing)")

    segments = json.loads(Path(args.input).read_text(encoding="utf-8"))
    splits = json.loads(Path(args.splits).read_text(encoding="utf-8"))
    if len(splits) != len(segments):
        raise SystemExit(f"❌ splits {len(splits)}개 ≠ 자막 단위 {len(segments)}개 (1:1이어야 함)")
    for i, (seg, parts) in enumerate(zip(segments, splits)):
        if not all(isinstance(p, str) for p in parts):
            raise SystemExit(f"❌ splits[{i}]는 텍스트 조각 배열이어야 합니다 (인덱스 아님): {parts!r}")
        if "".join(parts).replace(" ", "") != seg["text"].replace(" ", ""):
            print(f"  ⚠ splits[{i}] 글자가 원문과 다릅니다 — 분할 결과가 자막 텍스트로 쓰입니다:\n"
                  f"     원문: {seg['text']}\n     분할: {' | '.join(parts)}")
    for seg in segments:
        if "start" not in seg and seg.get("words"):
            seg["start"], seg["end"] = seg["words"][0]["start"], seg["words"][-1]["end"]

    parts = remove_overlaps(repair_parts(split_by_splits(segments, splits)), min_gap=0.02)
    look = {"style": args.style, "size": args.size, "font_id": args.font_id or None,
            "box_alpha": args.box_alpha, "font_path": None, "border_width": SUBTITLE_BORDER_WIDTH,
            "text_size": None}
    if args.style_from:
        look = style_from_project(Path(args.style_from), args.timeline)
        print(f"  🎨 자막 모양 가져옴: {args.style_from} → {look['style']}, 크기 {look['size']}, "
              f"박스 {look['box_alpha']}, 폰트 {'그대로' if not look['font_path'] else Path(look['font_path']).parent.parent.name}")
    edits = find_user_edits(parts, existing_subtitles(draft))
    if edits:
        print(f"  ✏️  CapCut에서 직접 고친 것으로 보이는 자막 {len(edits)}개"
              + (" — 그대로 유지합니다 (--keep-edits)" if args.keep_edits else " — 덮어씁니다 (유지하려면 --keep-edits)"))
        for part, old_text in edits[:10]:
            print(f"       {part['start']:7.2f}s  현재 {old_text!r} ← 새 자막 {part['text']!r}")
        if len(edits) > 10:
            print(f"       … 외 {len(edits) - 10}개")
        if args.keep_edits:
            for part, old_text in edits:
                part["text"] = old_text
    materials, track = build_text_track(parts, **look)
    for line in replace_subtitle_track(draft, materials, track, args.replace_track):
        print(line)

    print("\n💾 파일 저장 중...")
    write_4_files(proj, timeline_uuid, draft, write_root=write_root)
    print(f"\n✓ 자막 {len(track['segments'])}개 추가 (트랙 '{TRACK_NAME}'), 다른 트랙 {len(draft['tracks']) - 1}개 보존")


if __name__ == "__main__":
    main()
