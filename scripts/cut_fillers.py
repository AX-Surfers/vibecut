#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""이미 컷편집된 CapCut 타임라인에서 필러("어", "음", "네" 같은 군말) 구간을 잘라내고 뒤를 당긴다.

- 필러 시각은 Whisper 단어 JSON(원본 영상 시각)에서 찾는다. 여러 파일을 주면 합쳐서 본다.
- 모든 비디오 트랙을 같은 타임라인 구간으로 잘라 당긴다 (얼굴캠·화면 녹화가 일부 구간에만 있어도 된다).
- 자막(텍스트) 트랙은 잘린 만큼 줄이고 뒤를 당기며, 문구에 남은 필러 단어도 지운다.
- 비디오·텍스트가 아닌 트랙이 잘린 구간 뒤로 이어지면 같이 옮길 수 없어 멈춘다.

사용:
  uv run cut_fillers.py --project <CapCut 프로젝트> [--timeline "타임라인 01"] \\
      --words <stem>_words.json [--words <stem>_words.orig.json] [--min-dur 0.2] [--dry-run]
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _platform import check_capcut_not_running  # noqa: E402
from capcut_editor import (  # noqa: E402
    FPS,
    clone_video_segment,
    draft_path_for,
    frame_to_us,
    pick_video_track,
    resolve_timeline,
    write_4_files,
)

MIN_FRAMES = 13        # CapCut 최소 클립 길이 — 잘라낸 뒤 이보다 짧은 조각이 남으면 그 조각까지 같이 자른다
FILLER = re.compile(r"^(어+|음+|으음+|흠+|네+|넵)$")


def start_of(s: dict) -> int:
    return s["target_timerange"]["start"]


def end_of(s: dict) -> int:
    return start_of(s) + s["target_timerange"]["duration"]


def to_frame(us: int) -> int:
    return round(us * FPS / 1_000_000)


def strip_punct(text: str) -> str:
    return re.sub(r"[^\w가-힣]", "", text)


def filler_spans(word_files: list[str], min_dur: float) -> list[tuple[float, float, str]]:
    """원본 시각의 필러 단어 [(시작, 끝, 글자)] — 겹치는 것(파일 간 중복)은 하나로 합친다."""
    spans = []
    for f in word_files:
        for seg in json.loads(Path(f).read_text(encoding="utf-8")):
            for w in seg.get("words", []):
                t = strip_punct(w["word"])
                if FILLER.match(t) and w["end"] - w["start"] >= min_dur:
                    spans.append((w["start"], w["end"], t))
    spans.sort()
    merged: list[tuple[float, float, str]] = []
    for a, b, t in spans:
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b), merged[-1][2])
        else:
            merged.append((a, b, t))
    return merged


def to_timeline_frames(mains: list[dict], spans) -> list[tuple[int, int, str]]:
    """원본 시각 필러 → 타임라인 프레임 구간. 메인 클립 밖(이미 잘린 곳)이면 건너뛴다."""
    out = []
    for a, b, t in spans:
        mid = (a + b) / 2
        for s in mains:
            sa = s["source_timerange"]["start"] / 1e6
            sb = sa + s["source_timerange"]["duration"] / 1e6
            if sa <= mid <= sb:
                base = start_of(s) / 1e6 - sa
                fa, fb = round((max(a, sa) + base) * FPS), round((min(b, sb) + base) * FPS)
                if fb > fa:
                    out.append((fa, fb, t))
                break
    return out


def widen_for_slivers(ranges: list[list[int]], video_segs: list[dict]) -> None:
    """잘라낸 뒤 MIN_FRAMES보다 짧은 조각이 클립 가장자리에 남으면 그 조각까지 잘라낸다 (제자리 수정)."""
    changed = True
    while changed:
        changed = False
        for r in ranges:
            for s in video_segs:
                sa, sb = to_frame(start_of(s)), to_frame(end_of(s))
                if r[1] <= sa or r[0] >= sb:
                    continue
                if 0 < r[0] - sa < MIN_FRAMES:
                    r[0], changed = sa, True
                if 0 < sb - r[1] < MIN_FRAMES:
                    r[1], changed = sb, True
        ranges.sort()
        merged: list[list[int]] = []
        for r in ranges:
            if merged and r[0] <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], r[1])
            else:
                merged.append(r)
        if len(merged) != len(ranges):
            changed = True
        ranges[:] = merged


def main():
    ap = argparse.ArgumentParser(description="CapCut 타임라인에서 필러(어/음/네) 구간 자르기")
    ap.add_argument("--project", required=True, help="CapCut 프로젝트 디렉토리")
    ap.add_argument("--timeline", default=None)
    ap.add_argument("--words", action="append", required=True, help="Whisper 단어 JSON (여러 번 줄 수 있음)")
    ap.add_argument("--min-dur", type=float, default=0.2, help="이보다 짧게 인식된 단어는 오인식으로 보고 건너뜀 (기본 0.2초)")
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
    mi = pick_video_track(d)
    if mi is None:
        raise SystemExit("❌ 편집할 비디오 트랙을 찾지 못했습니다.")
    mains = sorted(d["tracks"][mi]["segments"], key=start_of)

    found = to_timeline_frames(mains, filler_spans(args.words, args.min_dur))
    if not found:
        print("자를 필러가 없습니다.")
        return
    video_tracks = [t for t in d["tracks"] if t["type"] == "video"]
    video_segs = [s for t in video_tracks for s in t["segments"]]
    ranges = [[a, b] for a, b, _ in found]
    widen_for_slivers(ranges, video_segs)
    cut_total = sum(b - a for a, b in ranges) / FPS
    print(f"✂ 필러 {len(found)}곳 → 잘라낼 구간 {len(ranges)}개, 합계 {cut_total:.2f}초")
    for a, b, t in found:
        print(f"   {a / FPS:8.2f}s ~ {b / FPS:8.2f}s  '{t}'")
    if args.dry_run:
        print("(dry-run — 쓰지 않음)")
        return

    cum = [0]
    for a, b in ranges:
        cum.append(cum[-1] + (b - a))

    def shift(frame: int) -> int:
        """원래 프레임 → 당겨진 프레임. 잘린 구간 안이면 그 구간의 시작으로 모은다."""
        out = frame
        for i, (a, b) in enumerate(ranges):
            if frame >= b:
                out = frame - cum[i + 1]
            elif frame > a:
                return a - cum[i]
        return out

    # 비디오·텍스트가 아닌 트랙은 첫 잘린 구간 뒤로 이어지면 같이 옮길 수 없다
    for t in d["tracks"]:
        if t["type"] not in ("video", "text") and any(end_of(s) / 1e6 * FPS > ranges[0][0] for s in t["segments"]):
            raise SystemExit(f"❌ 잘린 구간 뒤로 이어지는 {t['type']} 트랙이 있어 같이 옮길 수 없습니다")

    added: dict = {}
    old_mats: set[str] = set()
    for t in video_tracks:
        new_segs = []
        for s in sorted(t["segments"], key=start_of):
            sa, sb = to_frame(start_of(s)), to_frame(end_of(s))
            cuts = [(max(a, sa), min(b, sb)) for a, b in ranges if a < sb and b > sa]
            if not cuts:
                s["target_timerange"]["start"] = frame_to_us(shift(sa))
                new_segs.append(s)
                continue
            if abs((s.get("speed") or 1.0) - 1.0) > 1e-6 and "speed" in s:
                raise SystemExit("❌ 재생 속도가 바뀐 클립은 지원하지 않습니다")
            pieces, cur = [], sa
            for a, b in cuts:
                if a > cur:
                    pieces.append((cur, a))
                cur = b
            if cur < sb:
                pieces.append((cur, sb))
            old_mats |= {s["material_id"], *s.get("extra_material_refs", [])}
            for pa, pb in pieces:
                src = s["source_timerange"]["start"] + (frame_to_us(pa) - start_of(s))
                new_segs.append(clone_video_segment(s, vids[s["material_id"]], src, frame_to_us(pb) - frame_to_us(pa),
                                                    frame_to_us(shift(pa)), added, draft_materials=M))
        t["segments"] = new_segs

    # 자막: 잘린 만큼 줄이고 당긴다. 문구에 남은 필러 단어도 지운다.
    texts = {m["id"]: m for m in M.get("texts", [])}
    removed_tokens = dropped = 0
    for t in d["tracks"]:
        if t["type"] != "text":
            continue
        keep = []
        for s in sorted(t["segments"], key=start_of):
            na, nb = shift(to_frame(start_of(s))), shift(to_frame(end_of(s)))
            mat = texts.get(s.get("material_id"))
            if mat:
                c = json.loads(mat["content"])
                words = c["text"].split(" ")
                kept_words = [w for w in words if not FILLER.match(strip_punct(w))] if len(words) > 1 else words
                if len(kept_words) != len(words) and kept_words:
                    removed_tokens += len(words) - len(kept_words)
                    c["text"] = " ".join(kept_words)
                    for st in c.get("styles", []):
                        st["range"] = [0, len(c["text"])]
                    mat["content"] = json.dumps(c, ensure_ascii=False)
            if nb - na < 3:
                dropped += 1
                continue
            s["target_timerange"] = {"start": frame_to_us(na), "duration": frame_to_us(nb) - frame_to_us(na)}
            keep.append(s)
        for p, q in zip(keep, keep[1:]):
            if end_of(p) > start_of(q):
                p["target_timerange"]["duration"] = max(1, start_of(q) - start_of(p))
        t["segments"] = keep

    used = {r for t in d["tracks"] for s in t["segments"] for r in (s.get("material_id"), *s.get("extra_material_refs", []))}
    for key, lst in M.items():
        if isinstance(lst, list):
            M[key] = [m for m in lst if not (isinstance(m, dict) and m.get("id") in old_mats - used)]
    for key, lst in added.items():
        M.setdefault(key, []).extend(lst)
    d["duration"] = max(end_of(s) for t in d["tracks"] for s in t["segments"])
    print(f"   자막에서 지운 필러 단어 {removed_tokens}개, 잘린 구간에만 걸려 빠진 자막 {dropped}개")

    print("\n💾 파일 저장 중...")
    write_4_files(proj, tl, d, write_root=root)

    # 검증: 다시 읽어서
    d2 = json.loads(dp.read_text(encoding="utf-8"))
    ids = {m["id"] for lst in d2["materials"].values() if isinstance(lst, list) for m in lst if isinstance(m, dict) and "id" in m}
    problems = []
    for t in d2["tracks"]:
        ts = sorted(t["segments"], key=start_of)
        if any(end_of(a) > start_of(b) + 1 for a, b in zip(ts, ts[1:])):
            problems.append(f"{t['type']} 트랙 겹침")
        if any(r not in ids for s in ts for r in (s.get("material_id"), *s.get("extra_material_refs", [])) if r):
            problems.append(f"{t['type']} 트랙에 끊긴 소재 참조")
    m2 = sorted(d2["tracks"][mi]["segments"], key=start_of)
    if any(abs(end_of(a) - start_of(b)) > 1 for a, b in zip(m2, m2[1:])):
        problems.append("본영상 클립 사이 틈/겹침")
    if problems:
        print("❌ 검증 실패: " + " / ".join(sorted(set(problems))) + " — 백업에서 되돌리세요")
        sys.exit(1)
    print(f"✓ 본영상 {len(m2)}개 틈·겹침 0 / 끊긴 참조 0 / 전체 {d2['duration'] / 1e6 / 60:.2f}분 "
          f"(-{cut_total:.2f}초)")


if __name__ == "__main__":
    main()
