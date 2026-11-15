#!/usr/bin/env python3
"""이미 편집된 CapCut 타임라인을 클립 단위로 다시 전사한다 — 컷 뒤 NG 판단용.

왜: 원본 words.json의 Whisper 단어 시각은 멈춤을 가로질러 늘어난다. 그래서 컷 경계에 걸친 단어가
양쪽 클립에 다 잡혀 "참여 / 참여"처럼 반복으로 오판하고, 원본 전사의 오인식도 그대로 남는다
(claude meeting: "주영아" → 다시 들으니 "현재 참석자가"). 편집된 순서 그대로 소리를 이어 붙여 다시 전사하면
클립마다 실제로 남은 말과 정확한 원본 시각이 나온다.

- 본영상 클립 소리를 순서대로 이어 붙인다 (클립 사이 0.7초 무음 — 전사가 경계를 넘어 단어를 잇지 않게).
- 클립마다 그 소재의 {stem}_audio.wav에서 source_timerange만큼 읽는다 (소재가 여러 개여도 됨).
- transcribe.py --force로 전사 (타임라인이 바뀌었으니 캐시를 쓰면 안 됨).
- 출력: 클립 번호 · 타임라인 시각 · 단어@원본초. 말이 없는 클립은 (말 없음)으로 표시 → NG/빈 조각 후보.
  잘라낼 구간은 원본 초로 적어 tighten_pauses.py --remove '[[a,b],...]'에 넘긴다.

사용:
  uv run timeline_transcript.py --project <CapCut 프로젝트> [--timeline "타임라인 01"] \\
      [--from-clip 6] [--to-clip 40] [--out-dir <폴더>]
"""
import argparse
import json
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from capcut_editor import draft_path_for, pick_video_track, resolve_timeline  # noqa: E402
from speech import audio_for  # noqa: E402

GAP = 0.7  # 클립 사이 무음 (초)


def main():
    ap = argparse.ArgumentParser(description="편집된 타임라인을 클립 단위로 다시 전사")
    ap.add_argument("--project", required=True, help="CapCut 프로젝트 디렉토리")
    ap.add_argument("--timeline", default=None, help="타임라인이 여러 개면 이름 또는 id 앞부분")
    ap.add_argument("--from-clip", type=int, default=0, help="이 번호부터 (하이라이트 건너뛰기 등)")
    ap.add_argument("--to-clip", type=int, default=None, help="이 번호까지 (포함)")
    ap.add_argument("--out-dir", default=None, help="이어 붙인 wav·전사 저장 폴더 (기본: 임시 폴더)")
    args = ap.parse_args()

    proj = Path(args.project).expanduser()
    tl, _ = resolve_timeline(proj, args.timeline)
    d = json.loads(draft_path_for(proj, tl).read_text(encoding="utf-8"))
    vids = {m["id"]: m for m in d["materials"].get("videos", [])}
    mains = sorted(d["tracks"][pick_video_track(d)]["segments"], key=lambda s: s["target_timerange"]["start"])
    last = len(mains) - 1 if args.to_clip is None else args.to_clip
    picked = [(i, s) for i, s in enumerate(mains) if args.from_clip <= i <= last]

    out = Path(args.out_dir or Path(tempfile.gettempdir()) / "vibecut_timeline" / proj.name)
    out.mkdir(parents=True, exist_ok=True)
    wav_path = out / "timeline.wav"
    (out / "timeline_audio.wav").unlink(missing_ok=True)   # transcribe.py가 재사용하는 추출본 — 지난 타임라인 것이면 틀림
    srcs: dict = {}
    idx = []  # (클립 번호, 이어 붙인 wav 안 시작, 끝, 원본 시작, 타임라인 시작)
    with wave.open(str(wav_path), "wb") as w:
        pos = 0.0
        for i, s in picked:
            path = audio_for(Path(vids[s["material_id"]]["path"]))
            if path not in srcs:
                srcs[path] = wave.open(str(path))
                if not idx:
                    w.setparams(srcs[path].getparams())
            r = srcs[path]
            if (r.getframerate(), r.getnchannels()) != (w.getframerate(), w.getnchannels()):
                raise SystemExit(f"❌ 소재마다 오디오 형식이 다릅니다: {path}")
            sr = r.getframerate()
            a = s["source_timerange"]["start"] / 1e6
            dur = s["source_timerange"]["duration"] / 1e6
            r.setpos(min(int(a * sr), r.getnframes()))
            w.writeframes(r.readframes(int(dur * sr)))
            w.writeframes(b"\0" * w.getsampwidth() * w.getnchannels() * int(GAP * sr))
            idx.append((i, pos, pos + dur, a, s["target_timerange"]["start"] / 1e6))
            pos += dur + GAP
    for r in srcs.values():
        r.close()

    print(f"🎧 클립 {len(idx)}개 이어 붙임 ({pos:.0f}초) → 전사 중...", flush=True)
    subprocess.run(["uv", "run", "-q", str(Path(__file__).parent / "transcribe.py"), str(wav_path), "--force"], check=True)
    words = [x for seg in json.loads((out / "timeline_words.json").read_text(encoding="utf-8")) for x in seg["words"]]

    for i, p0, p1, a, t in idx:
        ws = [x for x in words if p0 - 0.1 <= (x["start"] + x["end"]) / 2 < p1 + 0.1]
        body = " ".join(f'{x["word"].strip()}@{a + x["start"] - p0:.2f}' for x in ws) or "(말 없음)"
        print(f"{i:3d} {int(t // 60)}:{t % 60:05.2f} [원본 {a:.2f}–{a + p1 - p0:.2f}] {body}")


if __name__ == "__main__":
    main()
