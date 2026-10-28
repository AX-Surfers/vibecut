#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""youtube_description.txt(vibecut-youtube-description 결과)를 읽어 업로드용 메타데이터를 JSON으로 뽑고 검증한다.

출력 JSON: {"titles": {"A": "...", ...}, "title": "고른 제목", "description": "...", "chapters": [[초, "이름"], ...],
            "problems": ["..."], "warnings": ["..."]}
problems가 있으면 업로드 전에 고쳐야 하고(종료 코드 1), warnings는 알려 주기만 한다.

사용:
  uv run youtube_meta.py <youtube_description.txt> [--title A|B|C|"직접 쓴 제목"]
"""
import argparse
import json
import re
import sys
from pathlib import Path

TITLE_MAX = 100          # 유튜브 제목 한도
DESC_MAX = 5000          # 설명 한도(바이트가 아니라 글자 수)
CHAPTER_MIN_COUNT = 3    # 챕터는 3개 이상이어야 유튜브가 챕터로 인식한다
CHAPTER_MIN_GAP_S = 10   # 챕터 사이 최소 길이


def split_sections(text: str) -> tuple[str, str]:
    """'제목 옵션'과 '유튜브 설명' 구역의 본문을 돌려준다."""
    parts = re.split(r"^=+\s*\n(.+?)\n=+\s*$", text, flags=re.M)
    # parts = [앞부분, 제목1, 본문1, 제목2, 본문2, ...]
    sections = {parts[i].strip(): parts[i + 1].strip() for i in range(1, len(parts) - 1, 2)}
    return sections.get("제목 옵션", ""), sections.get("유튜브 설명", "")


def parse_chapters(description: str) -> list[tuple[int, str]]:
    out = []
    for line in description.splitlines():
        m = re.match(r"^\s*(?:(\d{1,2}):)?(\d{1,2}):(\d{2})\s+(.+?)\s*$", line)
        if m:
            h, mi, s, name = m.groups()
            out.append((int(h or 0) * 3600 + int(mi) * 60 + int(s), name))
    return out


def build(text: str, title_arg: str | None) -> dict:
    titles_block, description = split_sections(text)
    titles = {m.group(1): m.group(2).strip() for m in re.finditer(r"^\[([A-Z])\]\s*(.+)$", titles_block, flags=re.M)}
    problems, warnings = [], []

    if title_arg is None:
        title = ""
        problems.append("제목을 고르지 않았습니다 (--title A|B|C 또는 직접 쓴 제목)")
    elif title_arg in titles:
        title = titles[title_arg]
    else:
        title = title_arg.strip()
    # 제목 옵션은 "[A] 결과 중심형 — "편집 시간 90%"" 꼴이라 따옴표 안만 제목이다
    q = re.search(r"[\"“'‘](.+?)[\"”'’]\s*$", title)
    if q and title_arg in titles:
        title = q.group(1).strip()

    if not description:
        problems.append("'유튜브 설명' 구역을 찾지 못했습니다")
    if title and len(title) > TITLE_MAX:
        problems.append(f"제목이 {len(title)}자입니다 (최대 {TITLE_MAX}자)")
    if len(description) > DESC_MAX:
        problems.append(f"설명이 {len(description)}자입니다 (최대 {DESC_MAX}자)")
    if re.search(r"[<>]", description + title):
        problems.append("제목·설명에 < 또는 > 가 있습니다 (유튜브가 거부합니다)")

    chapters = parse_chapters(description)
    if chapters:
        if chapters[0][0] != 0:
            problems.append("첫 챕터가 00:00이 아닙니다 (유튜브가 챕터로 인식하지 않습니다)")
        if len(chapters) < CHAPTER_MIN_COUNT:
            problems.append(f"챕터가 {len(chapters)}개입니다 (최소 {CHAPTER_MIN_COUNT}개)")
        if any(b[0] <= a[0] for a, b in zip(chapters, chapters[1:])):
            problems.append("챕터 시각이 오름차순이 아닙니다")
        if any(b[0] - a[0] < CHAPTER_MIN_GAP_S for a, b in zip(chapters, chapters[1:])):
            problems.append(f"챕터 사이가 {CHAPTER_MIN_GAP_S}초 미만인 곳이 있습니다")
    else:
        warnings.append("설명에 챕터(MM:SS 이름)가 없습니다")
    if len(titles) == 0 and title_arg in (None, "A", "B", "C"):
        warnings.append("'제목 옵션' 구역을 읽지 못했습니다")

    return {"titles": titles, "title": title, "description": description,
            "chapters": [[s, n] for s, n in chapters], "problems": problems, "warnings": warnings}


def self_check() -> None:
    sample = """==============================
제목 옵션
==============================

[A] 결과 중심형 — "편집 시간 90% 줄였습니다"
[B] 질문형 — "영상 편집 아직 직접 하시나요?"


==============================
유튜브 설명
==============================

후크 문장입니다.

⏱️ CHAPTERS
00:00 인트로
00:30 설치
01:15 결과
"""
    r = build(sample, "A")
    assert r["title"] == "편집 시간 90% 줄였습니다", r["title"]
    assert not r["problems"], r["problems"]
    assert [c[0] for c in r["chapters"]] == [0, 30, 75]
    bad = build(sample.replace("00:00 인트로", "00:05 인트로"), "B")
    assert any("00:00" in p for p in bad["problems"])
    assert build(sample, None)["problems"], "제목 미선택은 problems"
    long_title = build(sample, "가" * 101)
    assert any("101자" in p for p in long_title["problems"])
    print("✓ youtube_meta self-check")


def main():
    ap = argparse.ArgumentParser(description="youtube_description.txt → 업로드 메타데이터 JSON")
    ap.add_argument("file", nargs="?")
    ap.add_argument("--title", default=None, help="A/B/C 중 하나 또는 직접 쓴 제목")
    ap.add_argument("--self-check", action="store_true")
    args = ap.parse_args()
    if args.self_check:
        self_check()
        return
    if not args.file:
        ap.error("file이 필요합니다")
    result = build(Path(args.file).expanduser().read_text(encoding="utf-8"), args.title)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(1 if result["problems"] else 0)


if __name__ == "__main__":
    main()
