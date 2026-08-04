#!/usr/bin/env python3
"""Validate an LLM-generated Wiki working tree before it can be published."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import unquote


SOURCE_KEY_PATTERN = re.compile(r"^github:([0-9a-f]{40})$")
MARKDOWN_LINK_PATTERN = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    path: str
    message: str


def _git(repo: Path, *args: str, text: bool = True):
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        capture_output=True,
        text=text,
    )
    if result.returncode != 0:
        stderr = result.stderr if text else result.stderr.decode("utf-8", "replace")
        raise RuntimeError(stderr.strip() or "git command failed")
    return result.stdout


def _parse_name_status(raw: bytes) -> List[Dict[str, str]]:
    fields = raw.decode("utf-8", "surrogateescape").split("\0")
    if fields and fields[-1] == "":
        fields.pop()
    changes: List[Dict[str, str]] = []
    cursor = 0
    while cursor < len(fields):
        status = fields[cursor]
        cursor += 1
        if cursor >= len(fields):
            raise RuntimeError("malformed git name-status output")
        if status.startswith(("R", "C")):
            if cursor + 1 >= len(fields):
                raise RuntimeError("malformed git rename record")
            changes.append(
                {
                    "status": status,
                    "previous_path": fields[cursor],
                    "path": fields[cursor + 1],
                }
            )
            cursor += 2
        else:
            changes.append({"status": status, "path": fields[cursor]})
            cursor += 1
    return changes


def _changes(repo: Path, base_ref: str) -> List[Dict[str, str]]:
    tracked = _parse_name_status(
        _git(repo, "diff", "--name-status", "-z", base_ref, text=False)
    )
    known_paths = {item["path"] for item in tracked}
    untracked_raw = _git(
        repo, "ls-files", "--others", "--exclude-standard", "-z", text=False
    )
    for path in untracked_raw.decode("utf-8", "surrogateescape").split("\0"):
        if path and path not in known_paths:
            tracked.append({"status": "A", "path": path})
    return sorted(tracked, key=lambda item: item["path"])


def _allowed_path(path: str) -> bool:
    if path in {"docs/wiki/index.md", "docs/wiki/log.md"}:
        return True
    candidate = Path(path)
    return (
        path.startswith("docs/wiki/topics/")
        and candidate.suffix == ".md"
        and ".." not in candidate.parts
    )


def _base_bytes(repo: Path, base_ref: str, path: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(repo), "show", f"{base_ref}:{path}"],
        check=False,
        capture_output=True,
    )
    if result.returncode == 0:
        return result.stdout
    return b""


def _patch_size(repo: Path, base_ref: str, changes: Iterable[Dict[str, str]]) -> int:
    tracked_diff = _git(repo, "diff", "--binary", base_ref, text=False)
    size = len(tracked_diff)
    for change in changes:
        if change["status"] == "A":
            path = repo / change["path"]
            if path.is_file() and not _base_bytes(repo, base_ref, change["path"]):
                size += path.stat().st_size
    return size


def _inside(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _link_target(raw_target: str) -> Optional[str]:
    target = raw_target.strip()
    if target.startswith("<") and ">" in target:
        target = target[1 : target.index(">")]
    else:
        target = target.split(maxsplit=1)[0]
    if not target or target.startswith(("#", "http://", "https://", "mailto:")):
        return None
    return unquote(target.split("#", 1)[0])


def _wiki_graph(repo: Path) -> Tuple[Dict[Path, set[Path]], List[ValidationIssue]]:
    wiki_root = (repo / "docs/wiki").resolve()
    graph: Dict[Path, set[Path]] = {}
    issues: List[ValidationIssue] = []
    if not wiki_root.is_dir():
        return graph, issues
    for document in sorted(wiki_root.rglob("*.md")):
        resolved_document = document.resolve()
        graph.setdefault(resolved_document, set())
        content = document.read_text(encoding="utf-8")
        for raw_target in MARKDOWN_LINK_PATTERN.findall(content):
            target = _link_target(raw_target)
            if target is None:
                continue
            resolved_target = (document.parent / target).resolve()
            relative_document = document.relative_to(repo).as_posix()
            if not _inside(resolved_target, wiki_root):
                issues.append(
                    ValidationIssue(
                        "link_outside_wiki",
                        relative_document,
                        f"local link escapes docs/wiki: {target}",
                    )
                )
                continue
            if not resolved_target.is_file():
                issues.append(
                    ValidationIssue(
                        "broken_link",
                        relative_document,
                        f"local link target does not exist: {target}",
                    )
                )
                continue
            if resolved_target.suffix == ".md":
                graph[resolved_document].add(resolved_target)
    return graph, issues


def _reachable_topics(repo: Path, graph: Dict[Path, set[Path]]) -> set[Path]:
    index = (repo / "docs/wiki/index.md").resolve()
    if index not in graph:
        return set()
    visited: set[Path] = set()
    pending = [index]
    while pending:
        current = pending.pop()
        if current in visited:
            continue
        visited.add(current)
        pending.extend(graph.get(current, set()) - visited)
    topics_root = (repo / "docs/wiki/topics").resolve()
    return {path for path in visited if _inside(path, topics_root)}


def validate(
    repo: Path,
    base_ref: str,
    source_key: str,
    max_files: int,
    max_patch_bytes: int,
    incremental_base_ref: Optional[str] = None,
) -> List[ValidationIssue]:
    repo = repo.resolve()
    issues: List[ValidationIssue] = []
    source_match = SOURCE_KEY_PATTERN.fullmatch(source_key)
    if source_match is None:
        return [
            ValidationIssue(
                "invalid_source_key", "docs/wiki/log.md", "expected github:<40 hex SHA>"
            )
        ]
    head_sha = source_match.group(1)
    changes = _changes(repo, base_ref)
    if not changes:
        return issues
    incremental_ref = incremental_base_ref or base_ref
    incremental_changes = _changes(repo, incremental_ref)

    if len(changes) > max_files:
        issues.append(
            ValidationIssue(
                "too_many_files",
                ".",
                f"changed file count {len(changes)} exceeds {max_files}",
            )
        )
    patch_size = _patch_size(repo, base_ref, changes)
    if patch_size > max_patch_bytes:
        issues.append(
            ValidationIssue(
                "patch_too_large",
                ".",
                f"patch size {patch_size} exceeds {max_patch_bytes} bytes",
            )
        )

    for change in changes:
        candidate_paths = [change["path"]]
        if "previous_path" in change:
            candidate_paths.append(change["previous_path"])
        for path in candidate_paths:
            if not _allowed_path(path):
                issues.append(
                    ValidationIssue(
                        "forbidden_path", path, "Claude output may change only docs/wiki"
                    )
                )
        changed_path = repo / change["path"]
        if changed_path.is_symlink():
            issues.append(
                ValidationIssue(
                    "symlink_not_allowed",
                    change["path"],
                    "Wiki files must be regular files, not symbolic links",
                )
            )

    base_log = _base_bytes(repo, incremental_ref, "docs/wiki/log.md")
    log_path = repo / "docs/wiki/log.md"
    current_log = log_path.read_bytes() if log_path.is_file() else b""
    if source_key.encode("utf-8") in base_log:
        issues.append(
            ValidationIssue(
                "duplicate_source_key",
                "docs/wiki/log.md",
                f"source key already exists in base log: {source_key}",
            )
        )
    if not current_log.startswith(base_log):
        issues.append(
            ValidationIssue(
                "log_not_append_only",
                "docs/wiki/log.md",
                "existing log bytes must remain an exact prefix",
            )
        )
        appended_log = current_log
    else:
        appended_log = current_log[len(base_log) :]
    occurrences = appended_log.count(source_key.encode("utf-8"))
    if occurrences != 1:
        issues.append(
            ValidationIssue(
                "source_key_count",
                "docs/wiki/log.md",
                f"new log content must contain source key exactly once; found {occurrences}",
            )
        )

    graph, link_issues = _wiki_graph(repo)
    issues.extend(link_issues)
    topics_root = repo / "docs/wiki/topics"
    topic_files = (
        {path.resolve() for path in topics_root.rglob("*.md")}
        if topics_root.is_dir()
        else set()
    )
    reachable = _reachable_topics(repo, graph)
    for topic in sorted(topic_files - reachable):
        issues.append(
            ValidationIssue(
                "unreachable_topic",
                topic.relative_to(repo).as_posix(),
                "Topic is not reachable from docs/wiki/index.md",
            )
        )

    for change in incremental_changes:
        path = change["path"]
        if not path.startswith("docs/wiki/topics/") or change["status"].startswith("D"):
            continue
        topic_path = repo / path
        if not topic_path.is_file():
            continue
        content = topic_path.read_text(encoding="utf-8")
        if not re.search(r"(?m)^## Sources\s*$", content):
            issues.append(
                ValidationIssue(
                    "missing_sources", path, "changed Topic must contain a Sources section"
                )
            )
        if head_sha not in content:
            issues.append(
                ValidationIssue(
                    "missing_source_commit",
                    path,
                    "changed Topic must cite the source commit",
                )
            )
    return issues


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--base-ref", default="HEAD")
    parser.add_argument("--source-key", required=True)
    parser.add_argument("--incremental-base-ref")
    parser.add_argument("--max-files", type=int, default=30)
    parser.add_argument("--max-patch-bytes", type=int, default=500_000)
    parser.add_argument("--report", type=Path)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        changes = _changes(args.repo.resolve(), args.base_ref)
        issues = validate(
            args.repo,
            args.base_ref,
            args.source_key,
            args.max_files,
            args.max_patch_bytes,
            args.incremental_base_ref,
        )
    except (OSError, RuntimeError, UnicodeError) as error:
        print(f"validate-changes: {error}", file=sys.stderr)
        return 2
    report = {
        "changed": bool(changes),
        "changed_files": [item["path"] for item in changes],
        "issues": [asdict(issue) for issue in issues],
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 2 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
