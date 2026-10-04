"""Build a bounded, reproducible Topic inventory and retrieval candidates."""

from __future__ import annotations

import os
import posixpath
import re
import subprocess
from pathlib import Path
from typing import Optional
from urllib.parse import unquote

if __package__:
    from .topic_contract import ContractError, parse_topic_metadata, plain_markdown
    from .sync_wiki import MAX_TOTAL_FILES, MAX_TOTAL_BYTES
else:
    from topic_contract import ContractError, parse_topic_metadata, plain_markdown
    from sync_wiki import MAX_TOTAL_FILES, MAX_TOTAL_BYTES


def _git_bytes(repo: Path, *args: str) -> bytes:
    """Read Git output without shell interpretation and fail on invalid references."""
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True)
    if result.returncode:
        raise ContractError("cannot read Wiki snapshot")
    return result.stdout


def topic_links(content: str, path: str) -> list[str]:
    """Resolve inline and reference Markdown links, excluding fenced examples."""
    text = plain_markdown(content)
    definitions = {name.casefold(): target for name, target in re.findall(r"(?m)^\[([^\]]+)\]:\s*(\S.*)$", text)}
    targets = re.findall(r"\[[^\]]*\]\(([^)]+)\)", text)
    for name, reference in re.findall(r"\[([^\]]+)\]\[([^\]]*)\]", text):
        target = definitions.get((reference or name).casefold())
        if target:
            targets.append(target)
    links = set()
    for raw in targets:
        target = raw.strip()
        if target.startswith("<") and ">" in target:
            target = target[1:target.index(">")]
        else:
            target = target.split(maxsplit=1)[0] if target else ""
        if not target or target.startswith("#") or re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target):
            continue
        target = unquote(target.split("#", 1)[0])
        links.add(posixpath.normpath(posixpath.join(posixpath.dirname(path), target)))
    return sorted(links)


def _record(path: str, data: bytes) -> dict[str, object]:
    """Interpret one regular UTF-8 Topic and retain legacy evidence for retrieval."""
    try:
        content = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ContractError(f"{path}: Topic must be UTF-8") from error
    plain = plain_markdown(content)
    title = re.search(r"(?m)^#\s+(.+)$", plain)
    sources = [{"path": source, "sha": sha} for source, sha in re.findall(r"(?m)^- `([^`\n]+)` at `([0-9a-f]{40})`\s*$", plain)]
    return {"path": path, "title": title.group(1) if title else path,
            "metadata": parse_topic_metadata(content, path), "sources": sources,
            "links": topic_links(content, path)}


def build_topic_inventory(repo: Path, ref: Optional[str] = None) -> list[dict[str, object]]:
    """Read working Topics or a Git Wiki seed with the existing sync size limits."""
    records = []
    total_bytes = 0
    if ref is not None:
        entries = _git_bytes(repo, "ls-tree", "-r", "-z", ref, "--", "docs/wiki/topics").split(b"\0")
        files = []
        for entry in entries:
            if not entry:
                continue
            description, raw_path = entry.split(b"\t", 1)
            mode, kind, oid = description.split(b" ", 2)
            if mode not in (b"100644", b"100755") or kind != b"blob":
                raise ContractError("Wiki snapshot must contain regular files")
            path = raw_path.decode("utf-8")
            if path.endswith(".md"):
                files.append((path, oid.decode("ascii")))
        for path, oid in sorted(files):
            data = _git_bytes(repo, "cat-file", "blob", oid)
            total_bytes += len(data)
            if len(records) >= MAX_TOTAL_FILES or total_bytes > MAX_TOTAL_BYTES:
                raise ContractError("Topic inventory exceeds existing Wiki limits")
            records.append(_record(path, data))
    else:
        root = repo / "docs/wiki/topics"
        if any(parent.is_symlink() for parent in (repo / "docs", repo / "docs/wiki", root)):
            raise ContractError("Wiki inventory cannot follow symlinks")
        if not root.is_dir():
            return records
        for directory, directories, filenames in os.walk(root, followlinks=False):
            for name in directories + filenames:
                if (Path(directory) / name).is_symlink():
                    raise ContractError("Wiki inventory cannot follow symlinks")
            for name in sorted(filenames):
                document = Path(directory) / name
                if document.suffix != ".md":
                    continue
                size = document.stat().st_size
                if len(records) >= MAX_TOTAL_FILES or total_bytes + size > MAX_TOTAL_BYTES:
                    raise ContractError("Topic inventory exceeds existing Wiki limits")
                data = document.read_bytes()
                total_bytes += len(data)
                records.append(_record(document.relative_to(repo).as_posix(), data))
    return sorted(records, key=lambda item: item["path"])


def _tokens(text: str) -> set[str]:
    """Normalize CamelCase and multilingual words for retrieval, not policy identity."""
    expanded = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    return {token.casefold() for token in re.findall(r"[^\W_]+", expanded, re.UNICODE) if len(token) > 1}


def select_topic_candidates(inventory: list[dict[str, object]], changes: list[dict[str, str]], profile: dict[str, object], limit: int = 20) -> dict[str, object]:
    """Prefer direct evidence matches, exposing bounded retrieval without merging scopes."""
    if limit < 1:
        raise ContractError("candidate limit must be positive")
    paths = {item[key] for item in changes for key in ("path", "previous_path") if key in item}
    changed_tokens = _tokens(" ".join(sorted(paths)))
    scored = []
    for record in inventory:
        direct = bool(paths & {entry["path"] for entry in record.get("sources", [])})
        metadata = record.get("metadata") or {}
        text = record["path"] + " " + record.get("title", "") + " " + str(metadata.get("question", ""))
        scope = metadata.get("scope", {})
        if isinstance(scope, dict):
            for dimension, values in scope.items():
                if isinstance(values, list):
                    for value in values:
                        text += " " + str(value)
                        text += " " + " ".join(profile.get("aliases", {}).get(dimension, {}).get(value, []))
        overlap = len(changed_tokens & _tokens(text))
        if direct or overlap:
            scored.append((int(direct), overlap, record))
    scored.sort(key=lambda entry: (-entry[0], -entry[1], entry[2]["path"]))
    return {"topics": [entry[2] for entry in scored[:limit]], "truncated": len(scored) > limit, "checked_count": len(inventory)}


def build_reverse_relations(inventory: list[dict[str, object]]) -> dict[str, list[dict[str, str]]]:
    """Index incoming declared edges without treating related_to as causal impact."""
    reverse = {}
    for record in inventory:
        metadata = record.get("metadata") or {}
        source = metadata.get("id")
        if not isinstance(source, str) or not isinstance(metadata.get("relations"), list):
            continue
        for relation in metadata["relations"]:
            if isinstance(relation, dict) and isinstance(relation.get("target"), str) and isinstance(relation.get("type"), str):
                reverse.setdefault(relation["target"], []).append({"source": source, "type": relation["type"]})
    return {target: sorted(edges, key=lambda edge: (edge["source"], edge["type"])) for target, edges in sorted(reverse.items())}
