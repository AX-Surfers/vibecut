#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""현재 CapCut 타임라인 기준으로 자막을 한 문장 단위로 다시 만든다.

언제: 사용자가 영상을 손으로 고친 뒤 자막이 밀렸을 때(자막 트랙은 영상 편집을 따라가지 않는다),
      또는 소재가 여러 개(새 촬영 + 이전 영상)라서 subtitles_from_cuts.py를 못 쓸 때.
방식: 메인 영상 트랙의 클립(소재 경로·원본 구간·타임라인 위치)으로 words.json의 단어를 타임라인 시각에 얹고,
      문장 끝(다/요/죠/줘/마침표)에서 끊고, 길면 구절로 나눈다.
      --from 이전의 자막(사용자가 손으로 고친 부분)은 그대로 둔다.

사용:
  uv run resync_subtitles.py --project <프로젝트> --timeline "타임라인 01" --from 64.1 \\
     --source "DJI_face=새로운 녹음 30_words.json@26.46" --source "stab=영상_words.json@0" [--dry-run]
  --source 부분문자열=words.json@오프셋 : 소재 경로에 부분문자열이 있으면 (단어 시각 + 오프셋)이 그 소재의 원본 시각.
"""
import argparse, copy, json, math, re, sys
from pathlib import Path

SCRIPTS = Path("/Users/seungryk/vibecut/scripts")
sys.path.insert(0, str(SCRIPTS))
from capcut_editor import FPS, draft_path_for, frame_to_us, new_id, resolve_timeline, write_4_files  # noqa: E402

LINK = ("고", "는데", "니까", "면", "서", "지만", "고요", "인데", "해서", "한데", "에", "는", "를", "을", "도")
BAD_NEXT = {"게", "수", "거", "것", "때", "줄", "데", "번", "리", "만", "텐데", "뿐"}   # 이 말 앞에서는 끊지 않는다


def load_words(path: str) -> list[dict]:
    w = json.load(open(path, encoding="utf-8"))
    return [x for sg in w for x in sg.get("words", [])]


def map_words(draft: dict, sources: list[tuple[str, list[dict], float]]) -> list[dict]:
    """클립 안에 단어 '가운데 시점'이 들어오면 그 클립에 속한다 (시작·끝이 걸친 단어를 버리면 글자가 빠진다)."""
    vids = {m["id"]: m for m in draft["materials"]["videos"]}
    main = [t for t in draft["tracks"] if t["type"] == "video" and t.get("name") != "phone"][0]
    out = []
    for s in main["segments"]:
        path = vids[s["material_id"]]["path"]
        src = next((x for x in sources if x[0] in path), None)
        if not src:
            continue
        _, words, off = src
        st, tt = s["source_timerange"], s["target_timerange"]
        s0 = st["start"] / 1e6 - off; dur = st["duration"] / 1e6; t0 = tt["start"] / 1e6
        for x in words:
            mid = (x["start"] + x["end"]) / 2
            if s0 <= mid <= s0 + dur:
                a = t0 + max(0, x["start"] - s0); b = t0 + min(dur, x["end"] - s0)
                out.append({"w": x["word"].strip(), "a": a, "b": max(b, a + 0.05)})
    out.sort(key=lambda x: x["a"])
    return out


def sentences(words: list[dict]) -> list[list[dict]]:
    res, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        t = re.sub(r"[\"'”]", "", w["w"])
        end = t.endswith((".", "?", "!")) or t.rstrip(".?!,")[-1:] in "다요죠줘"
        if end and i + 1 < len(words) and words[i + 1]["w"].startswith(("라고", "라는", "라며")):
            end = False                                  # "할게요 라고" 는 한 문장
        if end or i + 1 == len(words):
            res.append(cur); cur = []
    return res


def split_sentence(ws: list[dict], maxc: int = 30) -> list[list[dict]]:
    txt = " ".join(x["w"] for x in ws)
    if len(txt) <= maxc:
        return [ws]                                      # 한 문장은 한 자막 (사용자 편집본은 26~28자 한 줄)
    n = math.ceil(len(txt) / 22); parts = []; start = 0
    for k in range(n - 1):
        tgt = len(" ".join(x["w"] for x in ws[start:])) / (n - k); best = None
        for j in range(start, len(ws) - 1):
            l = len(" ".join(x["w"] for x in ws[start:j + 1]))
            if l < 8:
                continue
            sc = abs(l - tgt) + (100 if ws[j + 1]["w"] in BAD_NEXT else 0) \
                - (6 if ws[j]["w"].endswith(",") or ws[j]["w"].rstrip(",").endswith(LINK) else 0)
            if best is None or sc < best[0]:
                best = (sc, j)
            if l > tgt + 8:
                break
        if best is None:
            break
        parts.append(ws[start:best[1] + 1]); start = best[1] + 1
    parts.append(ws[start:])
    j = 0
    while j < len(parts):                                # 9자 미만 조각은 이웃에 붙인다
        if len(parts) > 1 and len(" ".join(y["w"] for y in parts[j])) < 9:
            k = j - 1 if j > 0 else j + 1
            lo, hi = min(j, k), max(j, k); parts[lo:hi + 1] = [parts[lo] + parts[hi]]; j = 0
            continue
        j += 1
    return parts


def build(words: list[dict], cut: float, total: float, tail: float = 0.35, fill: float = 1.0) -> list[dict]:
    subs = []
    for s in sentences(words):
        if s[0]["a"] < cut:
            continue
        for p in split_sentence(s):
            subs.append({"a": p[0]["a"] - 0.05, "b": p[-1]["b"] + tail, "text": " ".join(x["w"] for x in p).strip()})
    subs.sort(key=lambda x: x["a"])
    for i, s in enumerate(subs):
        s["a"] = max(s["a"], subs[i - 1]["b"] if i else cut, cut)
        if i + 1 < len(subs) and subs[i + 1]["a"] - s["b"] <= fill:
            s["b"] = subs[i + 1]["a"]                    # 1초 이하 빈 곳은 메운다
        s["b"] = min(s["b"], total)
    return subs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True); ap.add_argument("--timeline", default=None)
    ap.add_argument("--from", dest="cut", type=float, required=True, help="이 시각(타임라인 초) 이후 문장부터 다시 만든다. 앞은 그대로")
    ap.add_argument("--source", action="append", required=True, help="부분문자열=words.json@오프셋")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    proj = Path(a.project).expanduser(); tl, root = resolve_timeline(proj, a.timeline)
    if not a.dry_run:
        from _platform import check_capcut_not_running; check_capcut_not_running()
    d = json.loads(draft_path_for(proj, tl).read_text(encoding="utf-8"))   # 쓰기 직전에 새로 읽는다
    sources = []
    for spec in a.source:
        key, rest = spec.split("=", 1); path, _, off = rest.partition("@")
        sources.append((key, load_words(path), float(off or 0)))
    subs = build(map_words(d, sources), a.cut, d["duration"] / 1e6)
    M = d["materials"]; mats = {m["id"]: m for m in M["texts"]}
    tr = max([t for t in d["tracks"] if t["type"] == "text"], key=lambda t: len(t["segments"]))
    segs = sorted(tr["segments"], key=lambda s: s["target_timerange"]["start"])
    keep = [s for s in segs if s["target_timerange"]["start"] / 1e6 < a.cut]; drop = [s for s in segs if s not in keep]
    print(f"유지 {len(keep)}개 / 교체 {len(drop)}개 → 새로 {len(subs)}개 (0.3초↑ 빈 곳 {sum(1 for x, y in zip(subs, subs[1:]) if y['a'] - x['b'] > 0.3)})")
    for s in subs[:5]:
        print(f"  {s['a']:.1f}-{s['b']:.1f} {s['text']}")
    if a.dry_run or not keep:
        return
    tmpl = keep[-1]; tm = mats[tmpl["material_id"]]; new_s, new_m = [], []
    for s in subs:
        t0, t1 = frame_to_us(round(s["a"] * FPS)), frame_to_us(round(s["b"] * FPS))
        if t1 - t0 < 100000:
            continue
        m = copy.deepcopy(tm); m["id"] = new_id(); c = json.loads(m["content"]); c["text"] = s["text"]
        for st in c["styles"]:
            st["range"] = [0, len(s["text"])]
        m["content"] = json.dumps(c, ensure_ascii=False)
        g = copy.deepcopy(tmpl); g["id"] = new_id(); g["material_id"] = m["id"]; g["target_timerange"] = {"start": t0, "duration": t1 - t0}
        new_m.append(m); new_s.append(g)
    if new_s:                                            # 유지한 마지막 자막이 새 첫 자막과 겹치면 줄인다
        e = keep[-1]["target_timerange"]["start"] + keep[-1]["target_timerange"]["duration"]; ns = new_s[0]["target_timerange"]["start"]
        if e > ns:
            keep[-1]["target_timerange"]["duration"] = max(100000, ns - keep[-1]["target_timerange"]["start"])
    drop_ids = {s["material_id"] for s in drop}
    tr["segments"] = keep + new_s
    M["texts"] = [m for m in M["texts"] if m["id"] not in drop_ids] + new_m
    write_4_files(proj, tl, d, write_root=root)
    print("저장 완료", len(new_s))


if __name__ == "__main__":
    main()
