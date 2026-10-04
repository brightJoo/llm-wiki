"""Verify classification explanations using Git fixtures and no model calls."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
import test_source_evidence as source_fixture
from test_topic_contract import demo_profile, topic_metadata
from scripts.wiki.classification_report import extract_classification, validate_classification, render_classification
from scripts.wiki.topic_contract import ContractError


class ClassificationReportTests(source_fixture.SourceEvidenceTests):
    """Exercise explanation accounting independent of generated prose quality."""

    def setUp(self):
        """Bind a complete no-durable-knowledge report to a real source batch."""
        super().setUp()
        self.profile = demo_profile()
        self.inventory = []
        self.base_inventory = []
        self.changed = set()
        self.key = f'github:{self.head}'
        self.report = {'schema_version': 1, 'source_key': self.key, 'head_sha': self.head,
                       'coverage': 'complete', 'decisions': [],
                       'ignored_changes': [{'path': p, 'reason': 'Synthetic change has no durable domain knowledge.'} for p in ('deleted.py', 'current.py')]}

    def codes(self):
        """Validate the current fixture and return its issue codes."""
        return {i.code for i in validate_classification(self.report, self.repo, self.base, self.key, self.inventory, self.profile, self.changed, self.base_inventory)}

    def add_decision(self, action='create'):
        """Add a scoped ranking Topic and its review explanation."""
        meta = topic_metadata()
        path = 'docs/wiki/topics/ranking.md'
        self.inventory = [{'path': path, 'metadata': meta, 'links': []}]
        self.changed = {path}
        decision = {'question': meta['question'], 'topic_id': meta['id'], 'action': action,
                    'candidates': [], 'rationale': 'Independent screen contract, not a shared policy.',
                    'scope': copy.deepcopy(meta['scope']), 'covered_changes': ['current.py'],
                    'evidence': [{'path': 'current.py', 'symbol': 'x', 'sha': self.head}],
                    'checked_dependents': [], 'unknowns': ['Production flags not verified.']}
        self.report['decisions'] = [decision]
        self.report['ignored_changes'] = self.report['ignored_changes'][:1]
        return decision

    def test_extracts_structured_or_exact_json_result(self):
        """Accept exact object transports and reject fences, prose and compiler errors."""
        for value in ({'structured_output': self.report}, {'result': json.dumps(self.report)}):
            self.assertEqual(extract_classification(value), self.report)
        for value in ({'result': '```json\n{}\n```'}, {'result': '{} trailing'}, {'is_error': True, 'structured_output': self.report}):
            with self.assertRaises(ContractError):
                extract_classification(value)

    def test_rejects_incomplete_even_for_empty_patch(self):
        """No Wiki edit cannot conceal unread input."""
        self.assertEqual(self.codes(), set())
        self.report['coverage'] = 'incomplete'
        self.assertIn('incomplete_classification', self.codes())

    def test_empty_patch_validator_still_checks_incomplete_report(self):
        """The public validator must reject incomplete input before its no-change return."""
        from scripts.wiki.validate_changes import validate
        profile_path = self.repo / '.llm-wiki/domain.json'
        profile_path.parent.mkdir()
        profile_path.write_text(json.dumps(self.profile), encoding='utf-8')
        self.head = self.commit('profile')
        self.key = f'github:{self.head}'
        self.report.update(head_sha=self.head, source_key=self.key, coverage='incomplete')
        self.report['ignored_changes'].append({'path': '.llm-wiki/domain.json', 'reason': 'Domain configuration only.'})
        with tempfile.TemporaryDirectory() as output:
            report_path = Path(output) / 'classification.json'
            report_path.write_text(json.dumps(self.report), encoding='utf-8')
            issues = validate(self.repo, 'HEAD', self.key, 30, 500_000, source_base_ref=self.base, classification_path=report_path)
        self.assertIn('incomplete_classification', {i.code for i in issues})

    def test_rejects_wrong_head_or_source_key(self):
        """Report identity is bound to trusted invocation values."""
        self.report['head_sha'] = self.base
        self.assertIn('classification_source_mismatch', self.codes())
        self.report['head_sha'] = self.head
        self.report['source_key'] = f'github:{self.base}'
        self.assertIn('classification_source_mismatch', self.codes())

    def test_rejects_missing_decision_for_changed_topic(self):
        """Every changed Topic needs an explanation with the matching action."""
        self.add_decision()
        self.assertEqual(self.codes(), set())
        self.report['decisions'] = []
        self.assertIn('unexplained_topic_change', self.codes())

    def test_rejects_unaccounted_changed_source_path(self):
        """Each relevant Git path needs a decision or explicit no-knowledge reason."""
        self.report['ignored_changes'] = []
        self.assertIn('unaccounted_source_change', self.codes())

    def test_proposal_cannot_change_existing_topic_boundary(self):
        """A proposal cannot silently rewrite existing identity, scope or relationships."""
        decision = self.add_decision('propose_structure')
        self.base_inventory = copy.deepcopy(self.inventory)
        self.inventory[0]['metadata']['scope']['surfaces'] = ['home', 'ranking']
        decision['scope'] = copy.deepcopy(self.inventory[0]['metadata']['scope'])
        self.assertIn('proposal_changed_boundary', self.codes())

    def test_rejects_invalid_evidence_and_scope(self):
        """A report with invented source evidence or wrong scope must fail."""
        d = self.add_decision()
        d['evidence'][0]['path'] = 'never.py'
        d['scope']['surfaces'] = []
        self.assertIn('invalid_classification_evidence', self.codes())
        self.assertIn('invalid_scope', self.codes())

    def test_renders_unknowns_and_unchanged_dependents_as_text(self):
        """Review text carries uncertainty and reasons without executing special strings."""
        d = self.add_decision()
        d['unknowns'] = ['$(touch /tmp/unwanted) <script>danger</script>']
        d['checked_dependents'] = [{'topic_id': 'home', 'action': 'unchanged', 'reason': 'Output contract unchanged.'}]
        rendered = render_classification(self.report)
        self.assertIn('Output contract unchanged.', rendered)
        self.assertIn('$(touch /tmp/unwanted)', rendered)
        self.assertNotIn('<script>', rendered)
        self.assertIn('semantic', rendered.lower())
