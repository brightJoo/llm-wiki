"""Verify evidence against a caller-bound, ancestor-checked Git source range."""
from __future__ import annotations
import re
import subprocess
from pathlib import Path
from typing import Optional
if __package__:
    from .topic_contract import ContractError
else:
    from topic_contract import ContractError


def _commit(repo: Path, ref: str) -> str:
    """Resolve an actual commit, rejecting revisions Git cannot safely resolve."""
    result = subprocess.run(['git', '-C', str(repo), 'rev-parse', '--verify', '--end-of-options', f'{ref}^{{commit}}'], capture_output=True)
    sha = result.stdout.decode('ascii', 'replace').strip()
    if result.returncode or not re.fullmatch('[0-9a-f]{40}', sha):
        raise ContractError('source reference must resolve to a commit')
    return sha


def validate_source_range(repo: Path, source_base_ref: str, head_sha: str) -> tuple[str, str]:
    """Return resolved bounds only when base is an ancestor of the fixed head."""
    base, head = _commit(repo, source_base_ref), _commit(repo, head_sha)
    result = subprocess.run(['git', '-C', str(repo), 'merge-base', '--is-ancestor', base, head], capture_output=True)
    if result.returncode:
        raise ContractError('source base must be an ancestor of source head')
    return base, head


def source_path_is_valid(repo: Path, path: str, head_sha: str, source_base_ref: Optional[str] = None) -> bool:
    """Accept files at head or proved removals in the batch, retaining legacy deletions."""
    if not isinstance(path, str) or not path or '\\' in path or '\0' in path or path.startswith('/') or re.match(r'^[A-Za-z]:', path) or any(part in ('', '.', '..') for part in path.split('/')):
        return False
    head = _commit(repo, head_sha)
    base = validate_source_range(repo, source_base_ref, head)[0] if source_base_ref is not None else None
    tree = subprocess.run(['git', '-C', str(repo), 'ls-tree', '-z', head, '--', f':(literal){path}'], capture_output=True)
    if tree.returncode:
        raise ContractError('cannot inspect source tree')
    if tree.stdout.startswith((b'100644 blob ', b'100755 blob ')):
        return True
    args = ['diff', '--no-ext-diff', '--no-textconv', '--name-status', '-z', '--no-renames', base, head] if base else ['diff-tree', '--root', '--no-commit-id', '--name-status', '-r', '-z', '--no-renames', head]
    result = subprocess.run(['git', '-C', str(repo), *args, '--', f':(literal){path}'], capture_output=True)
    if result.returncode:
        raise ContractError('cannot inspect source deletion evidence')
    entries = result.stdout.decode('utf-8', 'surrogateescape').split('\0')
    return any(entries[i] == 'D' and entries[i + 1] == path for i in range(0, len(entries) - 1, 2))
