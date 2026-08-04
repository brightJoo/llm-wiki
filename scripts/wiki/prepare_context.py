#!/usr/bin/env python3
"""Prepare a bounded, machine-readable context for one Wiki ingest run."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence


ZERO_SHA = "0" * 40
SOURCE_PATTERN = re.compile(r"github:([0-9a-f]{40})")
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9]+")
IGNORED_TOKENS = {
    "acceptance",
    "docs",
    "java",
    "javascript",
    "kotlin",
    "lld",
    "markdown",
    "python",
    "spec",
    "specs",
    "src",
    "test",
    "tests",
    "typescript",
}


class ContextError(RuntimeError):
    """Raised when a safe ingest context cannot be produced."""


def run_git(repo: Path, *args: str) -> str:
    """Run Git without a shell and return decoded stdout."""
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "git command failed"
        raise ContextError(detail)
    return result.stdout


def _run_git_bytes(repo: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip() or "git command failed"
        raise ContextError(detail)
    return result.stdout


def _commit_sha(repo: Path, ref: str) -> str:
    sha = run_git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}").strip()
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ContextError(f"ref did not resolve to a full commit SHA: {ref}")
    return sha


def _is_wiki_path(path: str) -> bool:
    return path == "docs/wiki" or path.startswith("docs/wiki/")


def _is_wiki_only_change(change: Dict[str, str]) -> bool:
    previous_path = change.get("previous_path")
    if previous_path is None:
        return _is_wiki_path(change["path"])
    return _is_wiki_path(change["path"]) and _is_wiki_path(previous_path)


def _parse_name_status(raw: bytes) -> List[Dict[str, str]]:
    fields = raw.decode("utf-8", "surrogateescape").split("\0")
    if fields and fields[-1] == "":
        fields.pop()
    changes: List[Dict[str, str]] = []
    index = 0
    while index < len(fields):
        status = fields[index]
        index += 1
        if index >= len(fields):
            raise ContextError("malformed git name-status output")
        if status.startswith(("R", "C")):
            if index + 1 >= len(fields):
                raise ContextError("malformed git rename record")
            previous_path = fields[index]
            path = fields[index + 1]
            index += 2
            changes.append(
                {"status": status, "path": path, "previous_path": previous_path}
            )
        else:
            path = fields[index]
            index += 1
            changes.append({"status": status, "path": path})
    return changes


def changed_files(repo: Path, base: str, head: str) -> List[Dict[str, str]]:
    raw = _run_git_bytes(repo, "diff", "--name-status", "-z", base, head)
    return _parse_name_status(raw)


def last_source_commit(log_path: Path) -> Optional[str]:
    if not log_path.is_file():
        return None
    matches = SOURCE_PATTERN.findall(log_path.read_text(encoding="utf-8"))
    return matches[-1] if matches else None


def seed_pending_wiki(
    repo: Path, main_ref: str, pending_ref: str
) -> Optional[str]:
    """Apply a managed pending branch's Wiki-only delta to the current tree."""
    repo = repo.resolve()
    main_sha = _commit_sha(repo, main_ref)
    pending_sha = _commit_sha(repo, pending_ref)
    commit_message = run_git(repo, "log", "-1", "--format=%B", pending_sha)
    if "LLM-Wiki-Managed: true" not in commit_message:
        raise ContextError(f"pending ref {pending_ref} is not managed by LLM Wiki")

    merge_base = run_git(repo, "merge-base", main_sha, pending_sha).strip()
    pending_changes = changed_files(repo, merge_base, pending_sha)
    non_wiki_paths = []
    pending_wiki_paths: set[str] = set()
    for change in pending_changes:
        paths = [change["path"]]
        if "previous_path" in change:
            paths.append(change["previous_path"])
        for path in paths:
            if _is_wiki_path(path):
                pending_wiki_paths.add(path)
            else:
                non_wiki_paths.append(path)
    if non_wiki_paths:
        raise ContextError(
            "pending ref contains non-Wiki changes: " + ", ".join(sorted(non_wiki_paths))
        )

    main_changes = changed_files(repo, merge_base, main_sha)
    main_wiki_paths: set[str] = set()
    for change in main_changes:
        paths = [change["path"]]
        if "previous_path" in change:
            paths.append(change["previous_path"])
        main_wiki_paths.update(path for path in paths if _is_wiki_path(path))
    overlap = sorted(main_wiki_paths & pending_wiki_paths)
    if overlap:
        raise ContextError(
            "main and pending Wiki changes overlap: " + ", ".join(overlap)
        )

    if pending_wiki_paths:
        patch = _run_git_bytes(
            repo,
            "diff",
            "--binary",
            merge_base,
            pending_sha,
            "--",
            "docs/wiki",
        )
        applied = subprocess.run(
            ["git", "-C", str(repo), "apply", "--whitespace=nowarn", "-"],
            input=patch,
            check=False,
            capture_output=True,
        )
        if applied.returncode != 0:
            detail = applied.stderr.decode("utf-8", "replace").strip()
            raise ContextError(f"pending Wiki patch did not apply: {detail}")

    cursor = last_source_commit(repo / "docs/wiki/log.md")
    if cursor:
        ancestor = subprocess.run(
            ["git", "-C", str(repo), "merge-base", "--is-ancestor", cursor, main_sha],
            check=False,
            capture_output=True,
        )
        if ancestor.returncode != 0:
            raise ContextError(
                f"pending source commit {cursor} is not an ancestor of {main_sha}"
            )
    return cursor


def _tokens(path: str) -> set[str]:
    return {
        token.lower()
        for token in TOKEN_PATTERN.findall(path)
        if len(token) > 2 and token.lower() not in IGNORED_TOKENS
    }


def _spec_candidates(repo: Path, paths: Sequence[str]) -> List[str]:
    specs_root = repo / "docs" / "specs"
    if not specs_root.is_dir():
        return []
    source_tokens: set[str] = set()
    for path in paths:
        source_tokens.update(_tokens(path))
    candidates: List[str] = []
    for spec_path in sorted(specs_root.rglob("*.md")):
        relative = spec_path.relative_to(repo).as_posix()
        if relative in paths or (_tokens(relative) & source_tokens):
            candidates.append(relative)
    return candidates


def _write_context(output_dir: Path, context: Dict[str, object], diff: bytes) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "context.json").write_text(
        json.dumps(context, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "changes.diff").write_bytes(diff)


def prepare_context(
    repo: Path,
    base: str,
    head: str,
    output_dir: Path,
    max_files: int,
    max_diff_bytes: int,
) -> Dict[str, object]:
    repo = repo.resolve()
    output_dir = output_dir.resolve()
    if max_files < 1 or max_diff_bytes < 1:
        raise ContextError("limits must be positive integers")

    head_sha = _commit_sha(repo, head)
    source_key = f"github:{head_sha}"
    if base == ZERO_SHA:
        context: Dict[str, object] = {
            "status": "skip",
            "reason": "bootstrap_required",
            "base_sha": base,
            "head_sha": head_sha,
            "source_key": source_key,
            "changed_files": [],
            "spec_candidates": [],
            "diff_path": str(output_dir / "changes.diff"),
        }
        _write_context(output_dir, context, b"")
        return context

    base_sha = _commit_sha(repo, base)
    ancestor = subprocess.run(
        ["git", "-C", str(repo), "merge-base", "--is-ancestor", base_sha, head_sha],
        check=False,
        capture_output=True,
    )
    if ancestor.returncode != 0:
        raise ContextError(f"base commit {base_sha} is not an ancestor of {head_sha}")

    all_changes = changed_files(repo, base_sha, head_sha)
    relevant_changes = [item for item in all_changes if not _is_wiki_only_change(item)]

    reason: Optional[str] = None
    if not all_changes:
        reason = "no_changes"
    elif not relevant_changes:
        reason = "docs_wiki_only"

    if len(relevant_changes) > max_files:
        raise ContextError(
            f"changed file count {len(relevant_changes)} exceeds limit {max_files}"
        )

    if relevant_changes:
        diff = _run_git_bytes(
            repo,
            "diff",
            "--binary",
            base_sha,
            head_sha,
            "--",
            ".",
            ":(exclude)docs/wiki/**",
        )
    else:
        diff = b""
    if len(diff) > max_diff_bytes:
        raise ContextError(
            f"diff size {len(diff)} exceeds limit {max_diff_bytes} bytes"
        )

    paths = [item["path"] for item in relevant_changes]
    context = {
        "status": "skip" if reason else "ready",
        "base_sha": base_sha,
        "head_sha": head_sha,
        "source_key": source_key,
        "changed_files": relevant_changes,
        "spec_candidates": _spec_candidates(repo, paths),
        "diff_path": str(output_dir / "changes.diff"),
    }
    if reason:
        context["reason"] = reason
    _write_context(output_dir, context, diff)
    return context


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--max-files", type=int, default=200)
    parser.add_argument("--max-diff-bytes", type=int, default=1_000_000)
    parser.add_argument("--pending-ref")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        base = args.base
        if args.pending_ref:
            pending_cursor = seed_pending_wiki(args.repo, args.head, args.pending_ref)
            if pending_cursor:
                base = pending_cursor
        context = prepare_context(
            args.repo,
            base,
            args.head,
            args.output_dir,
            args.max_files,
            args.max_diff_bytes,
        )
    except ContextError as error:
        print(f"prepare-context: {error}", file=sys.stderr)
        return 2
    print(json.dumps(context, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
