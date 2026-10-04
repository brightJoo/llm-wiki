"""Parse repository-owned domain profiles and scoped Markdown Topic contracts."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


PROFILE_PATH = ".llm-wiki/domain.json"
RELATION_TYPES = {"applies_policy", "uses_module", "exception_of", "depends_on", "related_to"}
TOPIC_FIELDS = {"schema_version", "id", "type", "question", "scope", "lifecycle", "relations"}
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")


class ContractError(RuntimeError):
    """An input cannot be safely interpreted as the declared contract."""


@dataclass(frozen=True)
class ContractIssue:
    """A caller-visible validation problem associated with a repository path."""

    code: str
    path: str
    message: str


def _unique_object(pairs):
    """Reject duplicate keys rather than silently choosing the last value."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ContractError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value):
    """Reject nonstandard JSON numeric values such as NaN."""
    raise ContractError(f"invalid JSON constant: {value}")


def load_json(content: str, path: str) -> dict[str, object]:
    """Read a strict JSON object, naming its origin on malformed input."""
    try:
        value = json.loads(content, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    except (ValueError, ContractError) as error:
        raise ContractError(f"{path}: {error}") from error
    if not isinstance(value, dict):
        raise ContractError(f"{path}: expected a JSON object")
    return value


def _identifier(value) -> bool:
    """Accept nonempty stable identifiers without whitespace or control characters."""
    return isinstance(value, str) and bool(value) and not any(c.isspace() or ord(c) < 32 for c in value)


def _strings(value) -> bool:
    """Check a nonempty, unique list of identifier strings without coercion."""
    return isinstance(value, list) and bool(value) and all(_identifier(item) for item in value) and len(set(value)) == len(value)


def parse_domain_profile(content: str, path: str) -> dict[str, object]:
    """Validate a v1 profile; aliases assist retrieval and never imply shared policy."""
    profile = load_json(content, path)
    required = {"schema_version", "id", "topic_types", "scope_dimensions", "relation_rules"}
    if not required <= profile.keys() or set(profile) - required - {"aliases"}:
        raise ContractError(f"{path}: invalid profile fields")
    if type(profile["schema_version"]) is not int or profile["schema_version"] != 1:
        raise ContractError(f"{path}: unsupported schema version")
    if not _identifier(profile["id"]) or not _strings(profile["topic_types"]):
        raise ContractError(f"{path}: invalid id or topic types")
    dimensions = profile["scope_dimensions"]
    if not isinstance(dimensions, dict) or not dimensions:
        raise ContractError(f"{path}: scope dimensions are required")
    for name, rule in dimensions.items():
        if not _identifier(name) or not isinstance(rule, dict) or set(rule) != {"required", "values"}:
            raise ContractError(f"{path}: invalid scope dimension")
        if type(rule["required"]) is not bool or not _strings(rule["values"]):
            raise ContractError(f"{path}: invalid scope values")
    aliases = profile.get("aliases", {})
    if not isinstance(aliases, dict):
        raise ContractError(f"{path}: aliases must be an object")
    for dimension, entries in aliases.items():
        if dimension not in dimensions or not isinstance(entries, dict):
            raise ContractError(f"{path}: undeclared alias dimension")
        for value, names in entries.items():
            if value not in dimensions[dimension]["values"] or not _strings(names):
                raise ContractError(f"{path}: alias refers to an undeclared value")
    rules = profile["relation_rules"]
    if not isinstance(rules, dict) or set(rules) - RELATION_TYPES:
        raise ContractError(f"{path}: unsupported relationship type")
    for rule in rules.values():
        if not isinstance(rule, dict) or set(rule) != {"from", "to"}:
            raise ContractError(f"{path}: invalid relationship rule")
        for endpoint in ("from", "to"):
            if not _strings(rule[endpoint]) or set(rule[endpoint]) - set(profile["topic_types"]):
                raise ContractError(f"{path}: undeclared relationship endpoint")
    return profile


def read_domain_profile(repo: Path, source_sha: str) -> Optional[dict[str, object]]:
    """Read the committed profile at source_sha; only an absent path returns None."""
    result = subprocess.run(["git", "-C", str(repo), "ls-tree", "-z", source_sha, "--", PROFILE_PATH], capture_output=True)
    if result.returncode:
        raise ContractError("cannot read source profile tree")
    if not result.stdout:
        return None
    if not result.stdout.startswith((b"100644 blob ", b"100755 blob ")):
        raise ContractError("domain profile must be a regular file")
    result = subprocess.run(["git", "-C", str(repo), "show", f"{source_sha}:{PROFILE_PATH}"], capture_output=True)
    if result.returncode:
        raise ContractError("cannot read source profile")
    try:
        content = result.stdout.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ContractError("domain profile must be UTF-8") from error
    return parse_domain_profile(content, PROFILE_PATH)


def markdown_sections(content: str) -> dict[str, list[str]]:
    """Collect level-two sections while respecting backtick and tilde fences."""
    sections = {}
    name = None
    body = []
    fence = None
    for line in content.splitlines(keepends=True):
        marker = FENCE.match(line.rstrip("\r\n"))
        if marker:
            token, tail = marker.groups()
            if fence is None:
                fence = (token[0], len(token))
            elif token[0] == fence[0] and len(token) >= fence[1] and not tail.strip():
                fence = None
            body.append(line)
            continue
        heading = re.match(r"^ {0,3}##[ \t]+(.+?)\s*$", line) if fence is None else None
        if heading:
            if name is not None:
                sections.setdefault(name, []).append("".join(body))
            name, body = heading.group(1), []
        else:
            body.append(line)
    if name is not None:
        sections.setdefault(name, []).append("".join(body))
    return sections


def plain_markdown(content: str) -> str:
    """Return non-fenced text for evidence and links, excluding quoted examples."""
    lines = []
    fence = None
    for line in content.splitlines(keepends=True):
        marker = FENCE.match(line.rstrip("\r\n"))
        if marker:
            token, tail = marker.groups()
            if fence is None:
                fence = (token[0], len(token))
            elif token[0] == fence[0] and len(token) >= fence[1] and not tail.strip():
                fence = None
            continue
        if fence is None:
            lines.append(line)
    return "".join(lines)


def parse_topic_metadata(content: str, path: str) -> Optional[dict[str, object]]:
    """Parse one real JSON metadata block, retaining unconverted legacy Topics."""
    sections = markdown_sections(content).get("Topic metadata", [])
    if not sections:
        return None
    if len(sections) != 1:
        raise ContractError(f"{path}: duplicate Topic metadata sections")
    blocks = re.findall(r"(?ms)^ {0,3}```json[ \t]*\r?\n(.*?)^ {0,3}```[ \t]*$", sections[0])
    if len(blocks) != 1:
        raise ContractError(f"{path}: expected one JSON metadata block")
    return load_json(blocks[0], path)


def validate_scope(scope, profile: dict[str, object], path: str) -> list[ContractIssue]:
    """Validate declared scope IDs; null stays unknown and empty arrays are invalid."""
    dimensions = profile["scope_dimensions"]
    if not isinstance(scope, dict):
        return [ContractIssue("invalid_scope", path, "scope must be an object")]
    issues = []
    for dimension, rule in dimensions.items():
        if rule["required"] and dimension not in scope:
            issues.append(ContractIssue("missing_scope", path, f"missing scope: {dimension}"))
    for dimension, value in scope.items():
        if dimension not in dimensions or (value is not None and (not _strings(value) or set(value) - set(dimensions[dimension]["values"]))):
            issues.append(ContractIssue("invalid_scope", path, f"invalid scope: {dimension}"))
    return issues


def validate_topic_metadata(metadata: dict[str, object], profile: dict[str, object], path: str) -> list[ContractIssue]:
    """Check all v1 fields and profile membership without claiming semantic correctness."""
    if not isinstance(metadata, dict) or set(metadata) != TOPIC_FIELDS:
        return [ContractIssue("invalid_topic_metadata", path, "expected all seven v1 metadata fields")]
    issues = []
    if type(metadata["schema_version"]) is not int or metadata["schema_version"] != 1:
        issues.append(ContractIssue("unsupported_topic_version", path, "expected schema version 1"))
    if not _identifier(metadata["id"]) or metadata["type"] not in profile["topic_types"]:
        issues.append(ContractIssue("invalid_topic_identity", path, "invalid id or type"))
    if not isinstance(metadata["question"], str) or not metadata["question"].strip():
        issues.append(ContractIssue("invalid_topic_question", path, "question is required"))
    if metadata["lifecycle"] not in ("active", "retired"):
        issues.append(ContractIssue("invalid_topic_lifecycle", path, "expected active or retired"))
    issues.extend(validate_scope(metadata["scope"], profile, path))
    relations = metadata["relations"]
    if not isinstance(relations, list):
        issues.append(ContractIssue("invalid_topic_relations", path, "relations must be a list"))
    else:
        seen = set()
        for relation in relations:
            if not isinstance(relation, dict) or set(relation) != {"type", "target"} or not _identifier(relation.get("type")) or not _identifier(relation.get("target")):
                issues.append(ContractIssue("invalid_topic_relations", path, "invalid relationship"))
                continue
            pair = (relation["type"], relation["target"])
            if relation["type"] not in profile["relation_rules"] or pair in seen:
                issues.append(ContractIssue("invalid_topic_relations", path, "undeclared or duplicate relationship"))
            seen.add(pair)
    return issues
