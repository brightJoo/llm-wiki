#!/usr/bin/env python3
"""Bind Wiki patch and classification bytes to an independently trusted source batch."""
from __future__ import annotations
import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional
if __package__:
    from .topic_contract import ContractError, load_json, read_domain_profile
    from .source_evidence import _commit, validate_source_range
    from .prepare_context import changed_files, _is_wiki_only_change
else:
    from topic_contract import ContractError, load_json, read_domain_profile
    from source_evidence import _commit, validate_source_range
    from prepare_context import changed_files, _is_wiki_only_change

V1_FIELDS = {'version', 'changed', 'bytes', 'sha256', 'base_ref', 'seed_tree'}
V2_FIELDS = V1_FIELDS | {'source_base_sha', 'source_head_sha', 'source_status', 'classification_sha256'}


def _hex(value, length=40) -> bool:
    """Check exact lowercase Git/checksum strings without accepting other JSON types."""
    return isinstance(value, str) and re.fullmatch(f'[0-9a-f]{{{length}}}', value) is not None


def build_manifest(patch: bytes, base_ref: str, seed_tree: str, source_base_sha: Optional[str] = None, source_head_sha: Optional[str] = None, source_status: Optional[str] = None, classification: Optional[bytes] = None) -> dict[str, object]:
    """Construct compatible v1 or range-bound v2 transport and enforce its shape."""
    value = {'version': 1, 'changed': bool(patch), 'bytes': len(patch), 'sha256': hashlib.sha256(patch).hexdigest(), 'base_ref': base_ref, 'seed_tree': seed_tree}
    if any(item is not None for item in (source_base_sha, source_head_sha, source_status)):
        value.update(version=2, source_base_sha=source_base_sha, source_head_sha=source_head_sha, source_status=source_status,
                     classification_sha256=hashlib.sha256(classification).hexdigest() if classification is not None else None)
    return validate_manifest(value, patch, classification)


def validate_manifest(value: dict[str, object], patch: bytes, classification: Optional[bytes] = None) -> dict[str, object]:
    """Reject malformed or altered transports before any publisher branch mutation."""
    if not isinstance(value, dict) or type(value.get('version')) is not int or value['version'] not in (1, 2) or set(value) != (V1_FIELDS if value['version'] == 1 else V2_FIELDS):
        raise ContractError('invalid patch metadata schema')
    if type(value['changed']) is not bool or type(value['bytes']) is not int or value['bytes'] != len(patch) or value['changed'] != bool(patch):
        raise ContractError('patch size/changed flag does not match metadata')
    if value['sha256'] != hashlib.sha256(patch).hexdigest():
        raise ContractError('patch checksum does not match metadata')
    if not isinstance(value['base_ref'], str) or not value['base_ref'] or any(c.isspace() or ord(c) < 32 for c in value['base_ref']):
        raise ContractError('invalid patch base ref')
    if value['seed_tree'] != 'absent' and not _hex(value['seed_tree']):
        raise ContractError('invalid Wiki seed tree')
    if value['version'] == 1:
        if classification is not None:
            raise ContractError('legacy manifest cannot carry a classification report')
        return value
    if not _hex(value['source_base_sha']) or not _hex(value['source_head_sha']) or value['source_status'] not in ('ready', 'no_changes', 'docs_wiki_only'):
        raise ContractError('invalid source bounds/status')
    if value['source_status'] == 'ready':
        if classification is None or not _hex(value['classification_sha256'], 64) or value['classification_sha256'] != hashlib.sha256(classification).hexdigest():
            raise ContractError('classification checksum missing or changed')
    elif patch or classification is not None or value['classification_sha256'] is not None:
        raise ContractError('skipped batch must have empty patch and no classification')
    return value


def source_status(repo: Path, base: str, head: str) -> str:
    """Derive readiness from trusted Git changes rather than compiler-owned context."""
    changes = changed_files(repo, base, head)
    if not changes:
        return 'no_changes'
    return 'ready' if any(not _is_wiki_only_change(item) for item in changes) else 'docs_wiki_only'


def validate_binding(value: dict[str, object], repo: Path, source_key: str, trusted_source_base: Optional[str]) -> None:
    """Re-read the fixed head's profile and verify v2 identity, bounds and status."""
    match = re.fullmatch(r'github:([0-9a-f]{40})', source_key)
    if match is None:
        raise ContractError('invalid source key')
    head = _commit(repo, match.group(1))
    profile = read_domain_profile(repo, head)
    if profile is not None and value['version'] != 2:
        raise ContractError('profiled source cannot downgrade to v1 artifact')
    if value['version'] == 2:
        if profile is None:
            raise ContractError('v2 artifact requires a profile at the compiled head')
        if not _hex(trusted_source_base):
            raise ContractError('v2 publication requires trusted SOURCE_BASE_SHA')
        base, _ = validate_source_range(repo, trusted_source_base, head)
        if value['source_head_sha'] != head or value['source_base_sha'] != base or _commit(repo, value['base_ref']) != head:
            raise ContractError('artifact source bounds do not match trusted invocation')
        if value['source_status'] != source_status(repo, base, head):
            raise ContractError('artifact source status does not match Git range')


def main(argv=None) -> int:
    """Create metadata or inspect it using only this trusted module's code."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('create', 'inspect'))
    parser.add_argument('--repo', type=Path, default=Path('.'))
    parser.add_argument('--patch', type=Path, required=True)
    parser.add_argument('--metadata', type=Path, required=True)
    parser.add_argument('--diff-base-ref')
    parser.add_argument('--source-head-ref')
    parser.add_argument('--source-base-ref')
    parser.add_argument('--source-key')
    parser.add_argument('--classification', type=Path)
    args = parser.parse_args(argv)
    try:
        patch = args.patch.read_bytes()
        classification = args.classification.read_bytes() if args.classification is not None else None
        if args.mode == 'create':
            if not args.diff_base_ref or not args.source_head_ref:
                raise ContractError('artifact creator requires seed and compiled head')
            head = _commit(args.repo, args.source_head_ref)
            result = subprocess.run(['git', '-C', str(args.repo), 'rev-parse', f'{_commit(args.repo, args.diff_base_ref)}:docs/wiki'], capture_output=True)
            seed = result.stdout.decode('ascii').strip() if result.returncode == 0 else 'absent'
            if read_domain_profile(args.repo, head) is not None:
                if args.source_base_ref is None:
                    raise ContractError('profiled artifact creator requires explicit source base')
                base, _ = validate_source_range(args.repo, args.source_base_ref, head)
                value = build_manifest(patch, head, seed, base, head, source_status(args.repo, base, head), classification)
            else:
                value = build_manifest(patch, args.source_head_ref, seed, classification=classification)
            args.metadata.parent.mkdir(parents=True, exist_ok=True)
            args.metadata.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        else:
            value = validate_manifest(load_json(args.metadata.read_text(encoding='utf-8'), str(args.metadata)), patch, classification)
            validate_binding(value, args.repo, args.source_key or '', args.source_base_ref)
            print('\t'.join([str(value['version']), 'true' if value['changed'] else 'false', value['base_ref'], value['seed_tree'], value.get('source_base_sha', 'none'), value.get('source_status', 'legacy')]))
    except (OSError, RuntimeError, UnicodeError) as error:
        print(f'artifact-manifest: {error}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
