#!/usr/bin/env python3
"""Copy an untrusted generated Wiki into a pristine validation checkout."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path
from typing import List, Optional, Sequence, Tuple


class SyncError(RuntimeError):
    """Raised when the generated Wiki tree is not safe to copy."""


def _allowed(relative: Path) -> bool:
    value = relative.as_posix()
    return value in {"index.md", "log.md"} or (
        value.startswith("topics/") and relative.suffix == ".md"
    )


def _collect(directory: Path, relative: Path = Path()) -> List[Tuple[Path, bytes]]:
    files: List[Tuple[Path, bytes]] = []
    with os.scandir(directory) as entries:
        for entry in sorted(entries, key=lambda item: item.name):
            child_relative = relative / entry.name
            if entry.is_symlink():
                raise SyncError(f"symbolic link is not allowed: {child_relative}")
            if entry.is_dir(follow_symlinks=False):
                if child_relative.parts[0] != "topics":
                    raise SyncError(f"directory is not allowed: {child_relative}")
                files.extend(_collect(Path(entry.path), child_relative))
            elif entry.is_file(follow_symlinks=False):
                if not _allowed(child_relative):
                    raise SyncError(f"file is not allowed: {child_relative}")
                files.append((child_relative, Path(entry.path).read_bytes()))
            else:
                raise SyncError(f"special file is not allowed: {child_relative}")
    return files


def sync_wiki(source: Path, target: Path) -> None:
    source_root = source.resolve()
    target_root = target.resolve()
    source_wiki = source_root / "docs/wiki"
    target_wiki = target_root / "docs/wiki"
    if source_root == target_root:
        raise SyncError("source and target must be different repositories")
    if not target_root.is_dir():
        raise SyncError("target repository does not exist")
    files = _collect(source_wiki) if source_wiki.is_dir() else []
    if target_wiki.exists():
        if target_wiki.is_symlink() or not target_wiki.is_dir():
            raise SyncError("target docs/wiki must be a directory")
        shutil.rmtree(target_wiki)
    for relative, content in files:
        destination = target_wiki / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        sync_wiki(args.source, args.target)
    except (OSError, SyncError) as error:
        print(f"sync-wiki: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
