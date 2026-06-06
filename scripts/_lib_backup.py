"""
Vibecut — CapCut 프로젝트 자동 백업 헬퍼

수정 작업 직전에 호출해 critical JSON 파일들만 tarball로 보관.
미디어는 제외하므로 용량 부담 최소 (보통 수 MB).

사용:
    from _lib_backup import backup_project_json

    backup_path = backup_project_json(project_dir, tag="add_subtitles")
    if backup_path:
        print(f"백업: {backup_path}")
"""
from __future__ import annotations

import shutil
import tarfile
import time
from pathlib import Path

# 백업 보관 위치 — 프로젝트 폴더가 통째로 삭제돼도 살아남도록 별도 디렉토리
BACKUP_ROOT = Path.home() / "Movies/CapCut/User Data/Projects/.vibecut_backups"

# 백업할 파일 패턴 (JSON + 메타 정보만 — 미디어 제외)
BACKUP_PATTERNS = [
    "draft_info.json",
    "draft_info.json.bak",
    "draft_meta_info.json",
    "draft_virtual_store.json",
    "Timelines/*/draft_info.json",
    "Timelines/*/draft_info.json.bak",
    "Timelines/project.json",
    "Timelines/project.json.bak",
]

# 프로젝트당 보관할 최대 백업 개수 (오래된 것 자동 정리)
MAX_BACKUPS_PER_PROJECT = 20


def _collect_files(project_dir: Path) -> list[Path]:
    """백업할 파일 목록 수집 (실재하는 것만)."""
    files: list[Path] = []
    for pattern in BACKUP_PATTERNS:
        if "*" in pattern:
            files.extend(project_dir.glob(pattern))
        else:
            p = project_dir / pattern
            if p.is_file():
                files.append(p)
    return sorted(set(files))


def _prune_old_backups(project_backup_dir: Path, keep: int = MAX_BACKUPS_PER_PROJECT) -> int:
    """오래된 백업 정리. 최근 `keep`개만 유지."""
    backups = sorted(project_backup_dir.glob("*.tar.gz"), key=lambda p: p.stat().st_mtime)
    pruned = 0
    while len(backups) > keep:
        oldest = backups.pop(0)
        try:
            oldest.unlink()
            pruned += 1
        except OSError:
            pass
    return pruned


def backup_project_json(project_dir: Path, tag: str = "auto",
                         quiet: bool = False) -> Path | None:
    """
    프로젝트의 critical JSON 파일들을 tarball로 백업.

    Args:
        project_dir: CapCut 프로젝트 디렉토리 (예: ~/Movies/CapCut/.../0601/)
        tag: 백업 파일명에 포함될 식별자 (예: "add_subtitles", "manual")
        quiet: True면 print 출력 억제

    Returns:
        생성된 백업 파일 경로, 또는 백업할 파일이 없으면 None
    """
    project_dir = Path(project_dir)
    if not project_dir.is_dir():
        if not quiet:
            print(f"  ⚠ 백업 건너뜀 — 프로젝트 없음: {project_dir}")
        return None

    files = _collect_files(project_dir)
    if not files:
        if not quiet:
            print(f"  ⚠ 백업 건너뜀 — JSON 파일 없음")
        return None

    project_name = project_dir.name
    project_backup_dir = BACKUP_ROOT / project_name
    project_backup_dir.mkdir(parents=True, exist_ok=True)

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    safe_tag = "".join(c if c.isalnum() or c in "-_" else "_" for c in tag)
    backup_path = project_backup_dir / f"{timestamp}_{safe_tag}.tar.gz"

    with tarfile.open(backup_path, "w:gz") as tar:
        for f in files:
            # 프로젝트 루트 기준 상대 경로로 보관 → 복원이 단순
            arcname = f.relative_to(project_dir)
            tar.add(f, arcname=str(arcname))

    pruned = _prune_old_backups(project_backup_dir)
    if not quiet:
        size_kb = backup_path.stat().st_size / 1024
        msg = f"  💾 백업: {backup_path.name} ({len(files)}개 파일, {size_kb:.0f}KB)"
        if pruned:
            msg += f" — 오래된 {pruned}개 정리"
        print(msg)

    return backup_path


def restore_latest(project_name: str, quiet: bool = False) -> Path | None:
    """
    지정 프로젝트의 가장 최근 백업을 복원.

    Returns:
        복원된 백업 파일 경로, 또는 백업이 없으면 None
    """
    project_backup_dir = BACKUP_ROOT / project_name
    if not project_backup_dir.is_dir():
        if not quiet:
            print(f"  ⚠ 백업 없음: {project_name}")
        return None

    backups = sorted(project_backup_dir.glob("*.tar.gz"))
    if not backups:
        if not quiet:
            print(f"  ⚠ 백업 없음: {project_name}")
        return None

    latest = backups[-1]
    capcut_projects = BACKUP_ROOT.parent
    project_dir = capcut_projects / project_name
    project_dir.mkdir(parents=True, exist_ok=True)

    with tarfile.open(latest, "r:gz") as tar:
        tar.extractall(project_dir)

    if not quiet:
        print(f"  ♻ 복원 완료: {latest.name} → {project_dir}")
    return latest


def list_backups(project_name: str | None = None) -> list[Path]:
    """백업 파일 목록 반환 (project_name이 None이면 전체)."""
    if not BACKUP_ROOT.is_dir():
        return []
    if project_name:
        d = BACKUP_ROOT / project_name
        return sorted(d.glob("*.tar.gz")) if d.is_dir() else []
    backups: list[Path] = []
    for sub in BACKUP_ROOT.iterdir():
        if sub.is_dir():
            backups.extend(sub.glob("*.tar.gz"))
    return sorted(backups)


if __name__ == "__main__":
    # CLI 모드 — 수동 백업/복원/목록
    import argparse
    parser = argparse.ArgumentParser(description="Vibecut 백업 유틸리티")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_b = sub.add_parser("backup", help="프로젝트 백업")
    p_b.add_argument("project_dir", help="CapCut 프로젝트 디렉토리")
    p_b.add_argument("--tag", default="manual")

    p_r = sub.add_parser("restore", help="최근 백업 복원")
    p_r.add_argument("project_name", help="프로젝트 이름 (폴더명)")

    p_l = sub.add_parser("list", help="백업 목록")
    p_l.add_argument("--project", default=None, help="특정 프로젝트만")

    args = parser.parse_args()
    if args.cmd == "backup":
        backup_project_json(Path(args.project_dir), tag=args.tag)
    elif args.cmd == "restore":
        restore_latest(args.project_name)
    elif args.cmd == "list":
        items = list_backups(args.project)
        if not items:
            print("(백업 없음)")
        for p in items:
            kb = p.stat().st_size / 1024
            print(f"  {p.parent.name:20s}  {p.name}  ({kb:.0f}KB)")
