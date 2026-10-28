#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""한 조각에 걸어 둔 설정(크로마키·크기·위치 등)을 같은 트랙의 나머지 조각 전부에 복사한다.

실전 배경: 사용자가 얼굴캠 첫 조각에만 CapCut에서 크로마키와 크기 40%를 넣고
"나머지 282개도 똑같이"라고 했다. CapCut에서는 한 조각씩 붙여 넣어야 해서 오래 걸린다.

- 효과(크로마키·필터 등)는 조각마다 소재를 따로 복제해 붙인다 (CapCut은 조각마다 제 소재를 갖는다).
- 이미 다른 값이 걸려 있던 조각은 기준 조각 값으로 교체한다.
- 시간·위치(target/source_timerange)는 절대 건드리지 않는다.

사용:
  uv run copy_clip_settings.py --project <프로젝트> [--timeline "타임라인 01"] \\
      --track DJI_xxx.MP4 [--from-clip 0] [--what all|effects|clip] [--dry-run]
"""
import argparse
import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _platform import check_capcut_not_running  # noqa: E402
from capcut_editor import draft_path_for, new_id, resolve_timeline, write_4_files  # noqa: E402

BASIC_KINDS = {"videos", "speeds", "placeholder_infos", "canvases",
               "sound_channel_mappings", "material_colors", "vocal_separations"}
CLIP_KEYS = ("clip", "uniform_scale", "volume", "last_nonzero_volume", "alpha")


def kind_of(materials: dict) -> dict:
    return {m["id"]: key for key, lst in materials.items() if isinstance(lst, list)
            for m in lst if isinstance(m, dict) and "id" in m}


def main():
    ap = argparse.ArgumentParser(description="한 조각의 설정을 트랙 전체에 복사")
    ap.add_argument("--project", required=True)
    ap.add_argument("--timeline", default=None)
    ap.add_argument("--track", required=True, help="대상 트랙의 영상 파일 이름 (예: DJI_xxx.MP4)")
    ap.add_argument("--from-clip", type=int, default=0, help="기준 조각 번호 (타임라인 순서, 0부터)")
    ap.add_argument("--what", choices=["all", "effects", "clip"], default="all",
                    help="복사할 것: effects(크로마키 등) / clip(크기·위치·볼륨) / all(기본)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    proj = Path(args.project).expanduser()
    tl, root = resolve_timeline(proj, args.timeline)
    dp = draft_path_for(proj, tl)
    if not args.dry_run:
        check_capcut_not_running()
    d = json.loads(dp.read_text(encoding="utf-8"))
    M = d["materials"]
    vids = {m["id"]: m for m in M.get("videos", [])}
    name = Path(args.track).name
    tracks = [t for t in d["tracks"] if t["type"] == "video" and t.get("segments")
              and all(Path(vids[s["material_id"]]["path"]).name == name for s in t["segments"])]
    if len(tracks) != 1:
        raise SystemExit(f"❌ '{name}'만 올라간 비디오 트랙을 {len(tracks)}개 찾았습니다")
    segs = sorted(tracks[0]["segments"], key=lambda s: s["target_timerange"]["start"])
    if not 0 <= args.from_clip < len(segs):
        raise SystemExit(f"❌ 기준 조각 번호가 범위 밖입니다 (0~{len(segs) - 1})")
    src = segs[args.from_clip]
    kinds = kind_of(M)
    src_effects = [(kinds[r], M[kinds[r]][[m["id"] for m in M[kinds[r]]].index(r)])
                   for r in src.get("extra_material_refs", []) if kinds.get(r) not in BASIC_KINDS]
    clip_vals = {k: copy.deepcopy(src[k]) for k in CLIP_KEYS if k in src}
    print(f"기준 조각 {args.from_clip} (타임라인 {src['target_timerange']['start']/1e6:.2f}초)")
    if args.what in ("all", "effects"):
        print("  효과:", ", ".join(f"{k}({m.get('color') or m.get('type', '')})" for k, m in src_effects) or "없음")
    if args.what in ("all", "clip"):
        print("  크기·위치:", json.dumps({k: v for k, v in clip_vals.items() if k != "last_nonzero_volume"}, ensure_ascii=False)[:160])

    changed_clip = changed_fx = 0
    for i, s in enumerate(segs):
        if i == args.from_clip:
            continue
        if args.what in ("all", "clip"):
            before = json.dumps({k: s.get(k) for k in clip_vals}, sort_keys=True)
            for k, v in clip_vals.items():
                s[k] = copy.deepcopy(v)
            changed_clip += before != json.dumps({k: s.get(k) for k in clip_vals}, sort_keys=True)
        if args.what in ("all", "effects"):
            refs = s.setdefault("extra_material_refs", [])
            old = [r for r in refs if kinds.get(r) not in BASIC_KINDS]
            for r in old:
                refs.remove(r)
            for key, mat in src_effects:
                clone = copy.deepcopy(mat)
                clone["id"] = new_id()
                M.setdefault(key, []).append(clone)
                refs.append(clone["id"])
            changed_fx += bool(src_effects) or bool(old)
    print(f"  → 크기·위치 {changed_clip}개 / 효과 {changed_fx}개 조각에 복사 (전체 {len(segs)}개)")
    if args.dry_run:
        print("(dry-run — 쓰지 않음)")
        return

    used = {r for t in d["tracks"] for s in t["segments"] for r in (s.get("material_id"), *s.get("extra_material_refs", []))}
    for key, lst in M.items():          # 아무도 안 쓰게 된 옛 효과 소재 정리
        if isinstance(lst, list) and key not in BASIC_KINDS:
            M[key] = [m for m in lst if not (isinstance(m, dict) and m.get("id") and m["id"] not in used
                                             and kinds.get(m["id"]) == key)]
    print("\n💾 파일 저장 중...")
    write_4_files(proj, tl, d, write_root=root)

    d2 = json.loads(dp.read_text(encoding="utf-8"))
    M2 = d2["materials"]; kinds2 = kind_of(M2)
    vids2 = {m["id"]: m for m in M2["videos"]}
    segs2 = sorted(next(t for t in d2["tracks"] if t["type"] == "video" and t["segments"]
                        and all(Path(vids2[s["material_id"]]["path"]).name == name for s in t["segments"]))["segments"],
                   key=lambda s: s["target_timerange"]["start"])
    ids = set(kinds2)
    fx_per_clip = [[r for r in s.get("extra_material_refs", []) if kinds2.get(r) not in BASIC_KINDS] for s in segs2]
    problems = []
    if args.what in ("all", "effects"):
        if any(len(fx) != len(src_effects) for fx in fx_per_clip):
            problems.append("효과 개수가 조각마다 다름")
        if len({tuple(sorted((k, v) for k, v in M2[kinds2[r]][[m["id"] for m in M2[kinds2[r]]].index(r)].items()
                             if k != "id")) for fx in fx_per_clip for r in fx}) > 1:
            problems.append("효과 값이 조각마다 다름")
    if args.what in ("all", "clip") and any(json.dumps({k: s.get(k) for k in clip_vals}, sort_keys=True)
                                            != json.dumps(clip_vals, sort_keys=True) for s in segs2):
        problems.append("크기·위치가 기준과 다름")
    if any(r not in ids for t in d2["tracks"] for s in t["segments"] for r in s.get("extra_material_refs", [])):
        problems.append("끊긴 소재 참조")
    if problems:
        print("❌ 검증 실패:", " / ".join(problems)); sys.exit(1)
    print(f"✓ {len(segs2)}개 조각 설정 동일 / 끊긴 참조 0")


if __name__ == "__main__":
    main()
