#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""원본 words.json + CapCut 컷 정보 → 타임라인 기준 subtitle_input.json

왜 이 방식인가 (실전 실패 사례):
  편집된 오디오를 이어붙여 다시 전사하면, Whisper는 하드컷을 무시하고 문장
  단위로 시각을 잡는다. 그 결과 자막이 컷 경계를 가로지르거나(앞 컷 꼬리에서
  다음 문장 자막이 미리 뜸), 컷 양쪽에서 같은 문장이 두 번 인식돼 자막이
  중복된다. 게다가 두 번째 전사와 오디오 재구성(ffconcat 오차) 비용도 든다.

  컷은 원본 words.json의 세그먼트 경계로 만들어졌다. 그 같은 단어 시각을
  컷의 source→target 매핑으로 옮기면 "자막 경계 == 컷 경계"가 구조적으로
  보장되고, 전사도 한 번이면 된다.

사용:
  uv run subtitles_from_cuts.py --words <stem>_words.json --project <CapCut 프로젝트> \\
      [--timeline "타임라인 02"] [--out <stem>_subtitle_input.json]

출력 형식은 add_subtitles.py --dump-only 와 동일 ({text, words, start, end} 배열,
시각은 타임라인 기준)이므로 이후 분할 에이전트 → apply_subtitles.py 흐름을 그대로 쓴다.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from capcut_editor import draft_path_for, pick_video_track, resolve_timeline  # noqa: E402


def load_cut_map(draft: dict) -> list[dict]:
    """편집 대상 트랙의 세그먼트를 source/target 구간 매핑으로 변환 (타임라인 순)."""
    idx = pick_video_track(draft)
    if idx is None:
        raise SystemExit("❌ 편집할 비디오 트랙을 찾지 못했습니다.")
    cuts = []
    for s in draft["tracks"][idx]["segments"]:
        src, tgt = s["source_timerange"], s["target_timerange"]
        cuts.append({
            "src_s": src["start"] / 1e6,
            "src_e": (src["start"] + src["duration"]) / 1e6,
            "tgt_s": tgt["start"] / 1e6,
            "tgt_e": (tgt["start"] + tgt["duration"]) / 1e6,
        })
    cuts.sort(key=lambda c: c["tgt_s"])
    return cuts


MIN_UNIT_SEC = 0.5   # 이보다 짧은 자막 단위는 같은 컷의 이웃 단위에 합친다 (깜빡임 방지)


def assign_words(segments: list[dict], cuts: list[dict]) -> dict[int, list[tuple[int, dict]]]:
    """각 단어를 **가장 많이 겹치는 컷**에 귀속. 반환: {컷 idx: [(세그먼트 idx, 단어), ...]}

    중점 기준을 쓰면 Whisper가 정적을 흡수시킨 단어("한" 38.88~39.54s, 컷은 정적
    시작 39.06s에서 끝남)가 어느 컷에도 못 들어가 자막에서 사라졌고("한 달"→"달"),
    시작 기준을 쓰면 시작 시각이 정적 안에 일찍 찍힌 단어("중이니까" 59.3s, 컷은
    60.01s부터)가 사라졌다. 겹치는 길이가 가장 긴 컷이 실제 발음이 들리는 컷이다.
    어떤 컷과도 안 겹치면 NG로 잘린 단어이므로 버린다.
    """
    by_cut: dict[int, list[tuple[int, dict]]] = {}
    for si, seg in enumerate(segments):
        for w in seg.get("words", []):
            if "start" not in w or "end" not in w:
                continue
            best, best_ov = None, 0.0
            for ci, c in enumerate(cuts):
                ov = min(w["end"], c["src_e"]) - max(w["start"], c["src_s"])
                if ov > best_ov:
                    best, best_ov = ci, ov
            if best is not None:
                by_cut.setdefault(best, []).append((si, w))
    return by_cut


def build_units(segments: list[dict], cuts: list[dict]) -> list[dict]:
    """(컷 × 원본 세그먼트) 교집합마다 자막 단위 하나를 만든다.

    - 단어는 가장 많이 겹치는 컷에 귀속한다 (assign_words 참고).
    - 컷의 첫 단위는 시작을 컷 시작에, 마지막 단위는 끝을 컷 끝에 스냅한다.
    - 같은 컷 안의 인접 단위는 겹치지 않게 앞 단위 끝을 뒤 단위 시작으로 제한한다.
    - MIN_UNIT_SEC보다 짧은 단위는 같은 컷의 앞(없으면 뒤) 단위에 합친다.
    - NG로 잘려나간 세그먼트는 어떤 컷에도 속하지 않으므로 자연히 빠진다.
    """
    by_cut = assign_words(segments, cuts)
    units = []
    for ci, c in enumerate(cuts):
        off = c["tgt_s"] - c["src_s"]
        clip_units: list[dict] = []
        for si, w in by_cut.get(ci, []):
            tw = {"word": w["word"],
                  "start": min(c["tgt_e"], max(c["tgt_s"], w["start"] + off)),
                  "end": min(c["tgt_e"], max(c["tgt_s"], w["end"] + off))}
            if clip_units and clip_units[-1]["seg"] == si:
                clip_units[-1]["words"].append(tw)
            else:
                clip_units.append({"seg": si, "words": [tw], "clip": ci})
        if not clip_units:
            continue
        for u in clip_units:
            u["start"], u["end"] = u["words"][0]["start"], u["words"][-1]["end"]
        # 너무 짧은 단위는 이웃에 합친다
        merged: list[dict] = []
        for u in clip_units:
            if merged and u["end"] - u["start"] < MIN_UNIT_SEC:
                merged[-1]["words"] += u["words"]
                merged[-1]["end"] = u["end"]
            elif merged and merged[-1]["end"] - merged[-1]["start"] < MIN_UNIT_SEC:
                u["words"] = merged[-1]["words"] + u["words"]
                u["start"] = merged[-1]["start"]
                merged[-1] = u
            else:
                merged.append(u)
        clip_units = merged
        clip_units[0]["start"] = c["tgt_s"]
        clip_units[-1]["end"] = c["tgt_e"]
        for a, b in zip(clip_units, clip_units[1:]):
            a["end"] = min(a["end"], b["start"])
        for u in clip_units:
            u["text"] = "".join(w["word"] for w in u["words"]).strip()
            del u["seg"]
        units.extend(u for u in clip_units if u["end"] > u["start"])
    return units


def check_alignment(units: list[dict], cuts: list[dict]) -> list[str]:
    """정렬 규칙 위반을 문장으로 돌려준다 (빈 리스트 = 통과)."""
    problems = []
    by_clip: dict[int, list[dict]] = {}
    for u in units:
        by_clip.setdefault(u["clip"], []).append(u)
        c = cuts[u["clip"]]
        if u["start"] < c["tgt_s"] - 1e-6 or u["end"] > c["tgt_e"] + 1e-6:
            problems.append(f"컷 {u['clip']} 경계 밖: {u['start']:.3f}~{u['end']:.3f} {u['text']!r}")
    for ci, us in by_clip.items():
        c = cuts[ci]
        if abs(us[0]["start"] - c["tgt_s"]) > 1e-6:
            problems.append(f"컷 {ci} 첫 자막이 컷 시작({c['tgt_s']:.3f})에 안 붙음: {us[0]['start']:.3f}")
        if abs(us[-1]["end"] - c["tgt_e"]) > 1e-6:
            problems.append(f"컷 {ci} 마지막 자막이 컷 끝({c['tgt_e']:.3f})에 안 붙음: {us[-1]['end']:.3f}")
        for a, b in zip(us, us[1:]):
            if a["end"] > b["start"] + 1e-6:
                problems.append(f"컷 {ci} 안에서 자막 겹침: {a['text']!r} / {b['text']!r}")
    return problems


def apply_corrections(units: list[dict]) -> int:
    """누적 오인식 사전(data/corrections.json)을 자막 텍스트와 단어에 적용.

    예전 경로(add_subtitles.py --dump-only)에서만 사전이 돌아 새 경로에서는
    "소수정의"→"소수정예" 같은 교정이 무용지물이었다. 단어 안에서 바뀌면 단어도
    같이 바꿔 split_by_splits의 텍스트↔단어 매핑이 어긋나지 않게 한다.
    """
    from add_subtitles import apply_corrections_dictionary
    _, n = apply_corrections_dictionary(units)
    if n:
        for u in units:
            for w in u["words"]:
                w["word"] = apply_corrections_dictionary([{"text": w["word"]}])[0][0]["text"]
        # ponytail: 여러 단어에 걸친 오인식은 텍스트만 고쳐지고 단어는 그대로다
        # (매핑은 greedy 길이 기준이라 대개 무해). 자주 걸리면 단어 재합성으로 확장.
    return n


def main():
    ap = argparse.ArgumentParser(description="원본 words.json을 컷 매핑으로 옮겨 자막 입력 생성")
    ap.add_argument("--words", required=True, help="원본 영상의 {stem}_words.json")
    ap.add_argument("--project", required=True, help="CapCut 프로젝트 디렉토리")
    ap.add_argument("--timeline", default=None, help="타임라인이 여러 개면 이름 또는 id 앞부분")
    ap.add_argument("--out", default=None, help="기본: <stem>_subtitle_input.json (words.json 옆)")
    ap.add_argument("--no-corrections", action="store_true", help="오인식 사전 적용 생략")
    args = ap.parse_args()

    proj = Path(args.project).expanduser()
    timeline_uuid, _ = resolve_timeline(proj, args.timeline)
    draft = json.loads(draft_path_for(proj, timeline_uuid).read_text(encoding="utf-8"))
    wp = Path(args.words)
    segments = json.loads(wp.read_text(encoding="utf-8"))

    cuts = load_cut_map(draft)
    units = build_units(segments, cuts)
    problems = check_alignment(units, cuts)
    if not args.no_corrections:
        n = apply_corrections(units)
        if n:
            print(f"오인식 사전 적용: {n}개 자막 교정")

    covered = {u["clip"] for u in units}
    print(f"컷 {len(cuts)}개 / 원본 세그먼트 {len(segments)}개 → 자막 단위 {len(units)}개")
    print(f"자막이 붙은 컷: {len(covered)}/{len(cuts)}"
          + ("" if len(covered) == len(cuts) else f"  (자막 없는 컷: {sorted(set(range(len(cuts))) - covered)})"))
    if problems:
        print("❌ 정렬 위반:")
        for p in problems:
            print("   -", p)
        sys.exit(1)
    print("✓ 모든 자막이 컷 경계 안에 있고, 각 컷의 첫/끝 자막이 컷 가장자리에 붙어 있음")

    out = [{k: u[k] for k in ("text", "words", "start", "end")} for u in units]
    stem = wp.name[:-len("_words.json")] if wp.name.endswith("_words.json") else wp.stem
    out_path = Path(args.out) if args.out else wp.with_name(stem + "_subtitle_input.json")
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"저장: {out_path}")


if __name__ == "__main__":
    main()
