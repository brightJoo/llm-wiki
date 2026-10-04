#!/usr/bin/env python3
"""Extract, verify and render reviewable domain classification decisions."""
from __future__ import annotations
import argparse
import html
import json
import re
import sys
from pathlib import Path
from typing import Optional
if __package__:
    from .topic_contract import ContractError, ContractIssue, load_json, read_domain_profile, validate_scope
    from .topic_inventory import build_topic_inventory, build_reverse_relations
    from .source_evidence import validate_source_range, source_path_is_valid
    from .prepare_context import changed_files, _is_wiki_only_change
else:
    from topic_contract import ContractError, ContractIssue, load_json, read_domain_profile, validate_scope
    from topic_inventory import build_topic_inventory, build_reverse_relations
    from source_evidence import validate_source_range, source_path_is_valid
    from prepare_context import changed_files, _is_wiki_only_change

REPORT_FIELDS = {'schema_version', 'source_key', 'head_sha', 'coverage', 'decisions', 'ignored_changes'}
DECISION_FIELDS = {'question', 'topic_id', 'action', 'candidates', 'rationale', 'scope', 'evidence', 'checked_dependents', 'unknowns', 'covered_changes'}
ACTIONS = ('update', 'create', 'unchanged', 'retire', 'propose_structure')


def _text(value) -> bool:
    """Require meaningful text without silently coercing other JSON types."""
    return isinstance(value, str) and bool(value.strip())


def _text_list(value) -> bool:
    """Accept a unique possibly empty list of meaningful strings."""
    return isinstance(value, list) and all(_text(item) for item in value) and len(set(value)) == len(value)


def extract_classification(compiler_result: dict[str, object]) -> dict[str, object]:
    """Accept a structured object or exact JSON result, never fenced or mixed prose."""
    if not isinstance(compiler_result, dict) or compiler_result.get('is_error'):
        raise ContractError('compiler result indicates failure')
    if 'structured_output' in compiler_result:
        report = compiler_result['structured_output']
        if not isinstance(report, dict):
            raise ContractError('structured_output must be an object')
        return report
    if not isinstance(compiler_result.get('result'), str):
        raise ContractError('compiler must return a classification JSON object')
    return load_json(compiler_result['result'], 'compiler result')


def validate_classification(report: dict[str, object], repo: Path, source_base_ref: str, source_key: str, inventory: list[dict[str, object]], profile: dict[str, object], changed_topic_paths: set[str], base_inventory: Optional[list[dict[str, object]]] = None) -> list[ContractIssue]:
    """Check accounting, scope and seeded/final causal impact; semantics need human review."""
    path = 'classification.json'
    if not isinstance(report, dict) or set(report) != REPORT_FIELDS or type(report.get('schema_version')) is not int or report['schema_version'] != 1:
        return [ContractIssue('invalid_classification_schema', path, 'expected exact v1 report fields')]
    match = re.fullmatch(r'github:([0-9a-f]{40})', source_key)
    if match is None:
        raise ContractError('invalid trusted source key')
    base, head = validate_source_range(repo, source_base_ref, match.group(1))
    issues = []
    if report['head_sha'] != head or report['source_key'] != source_key:
        issues.append(ContractIssue('classification_source_mismatch', path, 'report must match trusted source head and key'))
    if report['coverage'] != 'complete':
        issues.append(ContractIssue('incomplete_classification', path, 'incomplete input review cannot publish or advance cursor'))
    if not isinstance(report['decisions'], list) or not isinstance(report['ignored_changes'], list):
        return issues + [ContractIssue('invalid_classification_schema', path, 'decisions and ignored_changes must be arrays')]
    changes = [item for item in changed_files(repo, base, head) if not _is_wiki_only_change(item)]
    relevant = {item['path'] for item in changes}
    accounted = set()
    current = {record.get('metadata', {}).get('id'): record for record in inventory if isinstance(record.get('metadata'), dict) and _text(record['metadata'].get('id'))}
    old_by_path = {record['path']: record for record in (base_inventory or [])}
    candidates = set(current) | {record['path'] for record in inventory}
    candidates |= {record['metadata']['id'] for record in (base_inventory or []) if isinstance(record.get('metadata'), dict) and _text(record['metadata'].get('id'))}
    reverse = build_reverse_relations(inventory)
    seeded_reverse = build_reverse_relations(base_inventory or [])
    explained_paths, seen = set(), set()
    for decision in report['decisions']:
        if not isinstance(decision, dict) or set(decision) != DECISION_FIELDS:
            issues.append(ContractIssue('invalid_classification_decision', path, 'expected exact decision fields'))
            continue
        if not all(_text(decision[key]) for key in ('question', 'topic_id', 'rationale')) or decision['action'] not in ACTIONS:
            issues.append(ContractIssue('invalid_classification_decision', path, 'question, topic, action and rationale are required'))
            continue
        topic_id, action = decision['topic_id'], decision['action']
        if topic_id in seen:
            issues.append(ContractIssue('duplicate_classification_decision', path, f'duplicate decision: {topic_id}'))
        seen.add(topic_id)
        issues.extend(validate_scope(decision['scope'], profile, path))
        for key in ('candidates', 'unknowns', 'covered_changes'):
            if not _text_list(decision[key]):
                issues.append(ContractIssue('invalid_classification_decision', path, f'{key} must be a unique text array'))
        if _text_list(decision['candidates']) and set(decision['candidates']) - candidates:
            issues.append(ContractIssue('unknown_classification_candidate', path, 'candidate must identify a current or seeded Topic'))
        if _text_list(decision['covered_changes']):
            covered = set(decision['covered_changes'])
            if covered - relevant:
                issues.append(ContractIssue('invalid_covered_change', path, 'decision attributes paths outside the source batch'))
            accounted |= covered
        record = current.get(topic_id)
        if record is None:
            if action != 'propose_structure':
                issues.append(ContractIssue('missing_classification_topic', path, f'missing Topic: {topic_id}'))
        else:
            metadata = record['metadata']
            if decision['scope'] != metadata.get('scope') or decision['question'] != metadata.get('question'):
                issues.append(ContractIssue('classification_topic_mismatch', path, 'decision question/scope must match its Topic'))
            topic_path = record['path']
            previous = old_by_path.get(topic_path)
            if action == 'create' and previous or action == 'update' and base_inventory is not None and previous is None:
                issues.append(ContractIssue('classification_action_mismatch', path, 'create/update must match seeded path existence'))
            if action == 'retire' and metadata.get('lifecycle') != 'retired':
                issues.append(ContractIssue('classification_action_mismatch', path, 'retire requires retired lifecycle'))
            if action == 'unchanged' and topic_path in changed_topic_paths:
                issues.append(ContractIssue('classification_action_mismatch', path, 'unchanged Topic may not be edited'))
            if action == 'propose_structure' and previous and previous.get('metadata') != metadata:
                issues.append(ContractIssue('proposal_changed_boundary', topic_path, 'a structure proposal cannot mutate existing metadata'))
            if action != 'unchanged':
                explained_paths.add(topic_path)
        evidence = decision['evidence']
        if not isinstance(evidence, list) or not evidence:
            issues.append(ContractIssue('invalid_classification_evidence', path, 'each decision needs code evidence'))
        else:
            for entry in evidence:
                if not isinstance(entry, dict) or set(entry) != {'path', 'symbol', 'sha'} or not all(_text(entry.get(key)) for key in ('path', 'symbol', 'sha')) or entry['sha'] != head or not source_path_is_valid(repo, entry['path'], head, base):
                    issues.append(ContractIssue('invalid_classification_evidence', path, 'evidence needs a real source path, symbol description and fixed head SHA'))
        dependents, checked = decision['checked_dependents'], set()
        if not isinstance(dependents, list):
            issues.append(ContractIssue('invalid_checked_dependents', path, 'checked_dependents must be an array'))
        else:
            for entry in dependents:
                if not isinstance(entry, dict) or set(entry) != {'topic_id', 'action', 'reason'} or not _text(entry.get('topic_id')) or not _text(entry.get('reason')) or entry.get('action') not in ('update', 'unchanged', 'retire'):
                    issues.append(ContractIssue('invalid_checked_dependents', path, 'dependent needs Topic ID, action and reason'))
                    continue
                dependent = current.get(entry['topic_id'])
                if dependent is None or entry['topic_id'] in checked:
                    issues.append(ContractIssue('invalid_checked_dependents', path, 'dependent must be unique and present'))
                elif (dependent['path'] in changed_topic_paths) != (entry['action'] != 'unchanged'):
                    issues.append(ContractIssue('invalid_checked_dependents', path, 'dependent action must match actual edit status'))
                checked.add(entry['topic_id'])
            affected_edges = reverse.get(topic_id, []) + seeded_reverse.get(topic_id, [])
            required = {edge['source'] for edge in affected_edges if edge['type'] != 'related_to'} if action in ('update', 'retire') else set()
            if required - checked:
                issues.append(ContractIssue('unchecked_dependent', path, 'incoming policy/module dependents need an explicit check result'))
    for entry in report['ignored_changes']:
        if not isinstance(entry, dict) or set(entry) != {'path', 'reason'} or not _text(entry.get('path')) or not _text(entry.get('reason')):
            issues.append(ContractIssue('invalid_ignored_change', path, 'ignored change needs path and reason'))
        elif entry['path'] not in relevant or entry['path'] in accounted:
            issues.append(ContractIssue('invalid_ignored_change', path, 'ignored path must be a unique unassigned batch path'))
        else:
            accounted.add(entry['path'])
    for source_path in sorted(relevant - accounted):
        issues.append(ContractIssue('unaccounted_source_change', source_path, 'missing decision or no-durable-knowledge reason'))
    for topic_path in sorted(changed_topic_paths - explained_paths):
        issues.append(ContractIssue('unexplained_topic_change', topic_path, 'changed Topic needs a matching decision'))
    return issues


def _display(value) -> str:
    """Escape HTML and flatten newlines while keeping review data as literal text."""
    return html.escape(str(value), quote=False).replace('\r', '').replace('\n', ' ')


def render_classification(report: dict[str, object]) -> str:
    """Render review evidence; structural checks do not certify semantic correctness."""
    lines = ['## Classification review', '', 'Structural validation checks scope IDs, Git paths and accounting; it does not prove semantic policy equivalence.', '', f'- Source: {_display(report.get("source_key"))}', f'- Coverage: {_display(report.get("coverage"))}']
    for decision in report.get('decisions', []):
        lines += ['', f'### {_display(decision["topic_id"])} — {_display(decision["action"])}', '', f'- Question: {_display(decision["question"])}', f'- Candidates: {_display(", ".join(decision["candidates"]) or "None")}', f'- Reason: {_display(decision["rationale"])}', f'- Scope: {_display(json.dumps(decision["scope"], ensure_ascii=False, sort_keys=True))}']
        for entry in decision['evidence']:
            lines.append(f'- Evidence: {_display(entry["path"])} / {_display(entry["symbol"])} at {_display(entry["sha"])}')
        for entry in decision['checked_dependents']:
            lines.append(f'- Checked dependent: {_display(entry["topic_id"])} / {_display(entry["action"])} — {_display(entry["reason"])}')
        for unknown in decision['unknowns']:
            lines.append(f'- Unknown: {_display(unknown)}')
    for entry in report.get('ignored_changes', []):
        lines.append(f'- No durable change: {_display(entry["path"])} — {_display(entry["reason"])}')
    return '\n'.join(lines) + '\n'


def main(argv=None) -> int:
    """Extract and validate with trusted bounds before the engine writes the artifact."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path('.'))
    parser.add_argument('--source-base-ref')
    parser.add_argument('--source-key')
    parser.add_argument('--wiki-base-ref', default='HEAD')
    parser.add_argument('--compiler-result', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--render-report', type=Path)
    args = parser.parse_args(argv)
    try:
        if args.render_report is not None:
            print(render_classification(load_json(args.render_report.read_text(encoding='utf-8'), str(args.render_report))), end='')
            return 0
        if not all((args.source_base_ref, args.source_key, args.compiler_result, args.output)):
            raise ContractError('extraction requires source base/key, compiler result and output')
        if __package__:
            from .validate_changes import _changes
        else:
            from validate_changes import _changes
        profile = read_domain_profile(args.repo, args.source_key.removeprefix('github:'))
        if profile is None:
            raise ContractError('classification requires a committed domain profile')
        report = extract_classification(load_json(args.compiler_result.read_text(encoding='utf-8'), str(args.compiler_result)))
        changed = {item['path'] for item in _changes(args.repo, args.wiki_base_ref) if item['path'].startswith('docs/wiki/topics/')}
        issues = validate_classification(report, args.repo, args.source_base_ref, args.source_key, build_topic_inventory(args.repo), profile, changed, build_topic_inventory(args.repo, args.wiki_base_ref))
        if issues:
            raise ContractError('; '.join(f'{i.code}: {i.message}' for i in issues))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    except (OSError, RuntimeError, UnicodeError) as error:
        print(f'classification-report: {error}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
