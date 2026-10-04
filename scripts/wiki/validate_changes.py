#!/usr/bin/env python3
"""Validate an LLM-generated Wiki working tree before it can be published."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import unquote
if __package__:
    from .topic_contract import ContractError, read_domain_profile, validate_topic_graph, markdown_sections, plain_markdown
    from .topic_inventory import build_topic_inventory
    from .source_evidence import source_path_is_valid, validate_source_range
else:
    from topic_contract import ContractError, read_domain_profile, validate_topic_graph, markdown_sections, plain_markdown
    from topic_inventory import build_topic_inventory
    from source_evidence import source_path_is_valid, validate_source_range


SOURCE_KEY_PATTERN = re.compile(r"^github:([0-9a-f]{40})$")
MARKDOWN_LINK_PATTERN = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
REFERENCE_DEFINITION_PATTERN = re.compile(r"(?m)^\[([^\]]+)\]:\s*(\S.*)$")
REFERENCE_LINK_PATTERN = re.compile(r"\[([^\]]+)\]\[([^\]]*)\]")
APPENDED_LOG_PATTERN = re.compile(
    r"\n?## (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z) — "
    r"`(github:[0-9a-f]{40})`\n\n"
    r"- Topics: [^\n]+\n- Drift: [^\n]+\n?"
)
ANY_SOURCE_PATTERN = re.compile(r"github:[0-9a-f]{40}")
SOURCE_ENTRY_PATTERN = re.compile(
    r"(?m)^- `([^`\n]+)` at `([0-9a-f]{40})`\s*$"
)


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
        _git(
            repo,
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--name-status",
            "-z",
            base_ref,
            text=False,
        )
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
    tracked_diff = _git(
        repo,
        "diff",
        "--no-ext-diff",
        "--no-textconv",
        "--binary",
        base_ref,
        text=False,
    )
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
        definitions = {
            label.casefold(): target
            for label, target in REFERENCE_DEFINITION_PATTERN.findall(content)
        }
        raw_targets = list(MARKDOWN_LINK_PATTERN.findall(content))
        for label, reference in REFERENCE_LINK_PATTERN.findall(content):
            key = (reference or label).casefold()
            if key in definitions:
                raw_targets.append(definitions[key])
            else:
                issues.append(
                    ValidationIssue(
                        "broken_link",
                        document.relative_to(repo).as_posix(),
                        f"reference link has no definition: {key}",
                    )
                )
        for raw_target in raw_targets:
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


def _valid_source_path(repo: Path, source_sha: str, raw_path: str) -> bool:
    """Preserve the legacy helper while using the shared path evidence contract."""
    return source_path_is_valid(repo, raw_path, source_sha)


def validate(
    repo: Path,
    base_ref: str,
    source_key: str,
    max_files: int,
    max_patch_bytes: int,
    incremental_base_ref: Optional[str] = None,
    *,
    source_base_ref: Optional[str] = None,
    classification_path: Optional[Path] = None,
) -> List[ValidationIssue]:
    """Validate a proposed Wiki against its seed and a separately bound source range."""
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
    if source_base_ref is not None:
        validate_source_range(repo, source_base_ref, head_sha)
    incremental_ref = incremental_base_ref or base_ref
    incremental_changes = _changes(repo, incremental_ref)
    issues.extend(_domain_issues(repo, incremental_ref, head_sha, source_key, incremental_changes, source_base_ref, classification_path))
    if not changes:
        return issues

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
    try:
        appended_text = appended_log.decode("utf-8")
    except UnicodeDecodeError:
        appended_text = ""
    entry_match = APPENDED_LOG_PATTERN.fullmatch(appended_text)
    valid_timestamp = False
    if entry_match is not None:
        try:
            datetime.strptime(entry_match.group(1), "%Y-%m-%dT%H:%M:%SZ")
            valid_timestamp = True
        except ValueError:
            pass
    all_tokens = ANY_SOURCE_PATTERN.findall(appended_text)
    valid_entry = (
        entry_match is not None
        and valid_timestamp
        and entry_match.group(2) == source_key
        and all_tokens == [source_key]
    )
    if not valid_entry:
        issues.append(
            ValidationIssue(
                "invalid_log_entry",
                "docs/wiki/log.md",
                "new log content must contain one structured entry for the source key",
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
        source_sections = markdown_sections(content).get('Sources', [])
        if len(source_sections) != 1:
            issues.append(
                ValidationIssue(
                    "missing_sources", path, "changed Topic must contain a Sources section"
                )
            )
            sources = ""
        else:
            sources = plain_markdown(source_sections[0])
        source_entries = SOURCE_ENTRY_PATTERN.findall(sources)
        head_paths = [path for path, sha in source_entries if sha == head_sha]
        if not head_paths:
            issues.append(
                ValidationIssue(
                    "missing_source_commit",
                    path,
                    "changed Topic must cite the source commit",
                )
            )
        if not source_entries:
            issues.append(
                ValidationIssue(
                    "missing_source_path",
                    path,
                    "changed Topic must cite at least one repository path",
                )
            )
        elif head_paths and not all(
            source_path_is_valid(repo, source_path, head_sha, source_base_ref) for source_path in head_paths
        ):
            issues.append(
                ValidationIssue(
                    "invalid_source_path",
                    path,
                    "every head source path must exist at head or be removed inside the source range",
                )
            )
    return issues


def _domain_issues(repo, incremental_ref, head_sha, source_key, incremental_changes, source_base_ref, classification_path):
    """Validate profiled contracts even when the Wiki patch itself is empty."""
    issues = []
    try:
        profile = read_domain_profile(repo, head_sha)
        if profile is not None:
            inventory = build_topic_inventory(repo)
            base_inventory = build_topic_inventory(repo, incremental_ref)
            contract_issues = validate_topic_graph(inventory, profile, {item['path'] for item in incremental_changes}, base_inventory)
            if __package__:
                from .classification_report import validate_classification
                from .topic_contract import load_json
                from .prepare_context import changed_files, _is_wiki_only_change
            else:
                from classification_report import validate_classification
                from topic_contract import load_json
                from prepare_context import changed_files, _is_wiki_only_change
            ready = source_base_ref is not None and any(not _is_wiki_only_change(item) for item in changed_files(repo, source_base_ref, head_sha))
            if classification_path is not None:
                if source_base_ref is None:
                    raise ContractError('classification requires a trusted source base')
                report = load_json(classification_path.read_text(encoding='utf-8'), str(classification_path))
                changed_topics = {item['path'] for item in incremental_changes if item['path'].startswith('docs/wiki/topics/')}
                contract_issues.extend(validate_classification(report, repo, source_base_ref, source_key, inventory, profile, changed_topics, base_inventory))
            elif ready:
                issues.append(ValidationIssue('missing_classification_report', 'classification.json', 'profiled source changes require a complete report, even for an empty patch'))
            issues.extend(ValidationIssue(item.code, item.path, item.message) for item in contract_issues)
    except ContractError as error:
        issues.append(ValidationIssue('invalid_topic_contract', 'docs/wiki/topics', str(error)))
    return issues


def _parser() -> argparse.ArgumentParser:
    """Define compatible CLI options plus explicitly bound source/report inputs."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--base-ref", default="HEAD")
    parser.add_argument("--source-key", required=True)
    parser.add_argument("--incremental-base-ref")
    parser.add_argument("--source-base-ref")
    parser.add_argument("--classification-report", type=Path)
    parser.add_argument("--max-files", type=int, default=30)
    parser.add_argument("--max-patch-bytes", type=int, default=500_000)
    parser.add_argument("--report", type=Path)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Write deterministic validation diagnostics and return nonzero for any issue."""
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
            source_base_ref=args.source_base_ref,
            classification_path=args.classification_report,
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
