"""Run trusted publisher checks with local Git remotes and a fake PR API."""
import json
import unittest
import test_ingest_pipeline as pipeline
from test_topic_contract import demo_profile, topic_metadata, metadata_document


class ProfiledPublisherTests(unittest.TestCase):
    """Verify failures leave branches/cursors and remote PR writes untouched."""

    def setUp(self):
        """Create the existing engine fixture and a real profiled source batch."""
        self.f = pipeline.PublisherTests('runTest')
        self.f.setUp()
        self.addCleanup(self.f.tearDown)
        self.profile = demo_profile()
        self.f.write_seed('.llm-wiki/domain.json', json.dumps(self.profile))
        pipeline.git(self.f.seed, 'add', '.')
        pipeline.git(self.f.seed, 'commit', '-m', 'profile')
        self.source_base = pipeline.git(self.f.seed, 'rev-parse', 'HEAD')
        self.f.write_seed('src/search.py', 'def search():\n    return ["current"]\n')
        pipeline.git(self.f.seed, 'add', '.')
        pipeline.git(self.f.seed, 'commit', '-m', 'source change')
        self.f.base = self.f.head_sha = pipeline.git(self.f.seed, 'rev-parse', 'HEAD')
        self.f.source_key = f'github:{self.f.head_sha}'
        pipeline.git(self.f.seed, 'push', 'origin', 'main')
        self.report_path = self.f.artifact_dir / 'classification.json'

    def build_profiled(self, changed=True, coverage='complete'):
        """Produce a fake compiler proposal and use the real artifact creator."""
        meta = topic_metadata('display.screen.search')
        meta['question'] = 'What does the synthetic search screen return?'
        report = {'schema_version': 1, 'source_key': self.f.source_key, 'head_sha': self.f.head_sha,
                  'coverage': coverage, 'decisions': [], 'ignored_changes': []}
        if changed:
            self.f.write_seed('docs/wiki/topics/search.md', metadata_document(meta) + '\n## Current behavior\n\nCurrent synthetic result.\n\n## Sources\n\n' + f'- `src/search.py` at `{self.f.head_sha}`\n  search: returns the final result.\n')
            path = self.f.seed / 'docs/wiki/log.md'
            path.write_text(path.read_text(encoding='utf-8') + f'\n## 2026-10-04T00:00:00Z — `{self.f.source_key}`\n\n- Topics: search\n- Drift: None observed\n', encoding='utf-8')
            report['decisions'] = [{'question': meta['question'], 'topic_id': meta['id'], 'action': 'update',
                                    'candidates': ['docs/wiki/topics/search.md'], 'rationale': 'Migrate existing screen instead of duplicating it.',
                                    'scope': meta['scope'], 'covered_changes': ['src/search.py'],
                                    'evidence': [{'path': 'src/search.py', 'symbol': 'search', 'sha': self.f.head_sha}],
                                    'checked_dependents': [], 'unknowns': ['External configuration not verified.']}]
        else:
            report['ignored_changes'] = [{'path': 'src/search.py', 'reason': 'No durable synthetic knowledge changed.'}]
        self.report_path.parent.mkdir(parents=True, exist_ok=True)
        self.report_path.write_text(json.dumps(report), encoding='utf-8')
        result = pipeline.run(str(pipeline.CREATE_PATCH), self.f.base, str(self.f.patch), str(self.f.metadata), self.f.head_sha, self.source_base, str(self.report_path), cwd=self.f.seed)
        self.assertEqual(result.returncode, 0, result.stderr)
        pipeline.git(self.f.seed, 'reset', '--hard', self.f.base)
        return report

    def publish_profiled(self, clone):
        """Provide trusted source bounds separately from the artifact metadata."""
        env = self.f.publisher_env()
        env['SOURCE_BASE_SHA'] = self.source_base
        return pipeline.run(str(pipeline.PUBLISH_PR), str(self.f.patch), str(self.f.metadata), 'main', 'wiki/pending', self.f.source_key, str(self.report_path), cwd=clone, env=env)

    def assert_rejected_without_mutation(self, clone):
        """Ensure a failed verification does not create a cursor or write a PR."""
        before = pipeline.git(clone, 'rev-parse', 'HEAD')
        result = self.publish_profiled(clone)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertEqual(pipeline.git(clone, 'rev-parse', 'HEAD'), before)
        self.assertEqual(pipeline.git(clone, 'status', '--porcelain'), '')
        self.assertEqual(pipeline.git(self.f.seed, 'ls-remote', '--heads', 'origin', 'refs/heads/wiki/pending'), '')
        self.assertEqual(json.loads(self.f.fake_state.read_text())['calls'], [])
        return result

    def test_rejects_tampered_report_before_branch_mutation(self):
        """Changed report bytes must fail before publisher worktree or remote writes."""
        self.build_profiled()
        self.report_path.write_text('{}', encoding='utf-8')
        self.assert_rejected_without_mutation(self.f.clone_publisher())

    def test_incomplete_empty_patch_does_not_persist_cursor(self):
        """An empty patch with incomplete input review cannot be marked processed."""
        self.build_profiled(False, 'incomplete')
        self.assert_rejected_without_mutation(self.f.clone_publisher())

    def test_revalidates_profile_from_compiled_head_when_main_advances(self):
        """A later main profile does not replace the source snapshot being verified."""
        self.build_profiled()
        self.profile['scope_dimensions']['surfaces']['values'] = ['home']
        self.profile['aliases'] = {}
        self.f.write_seed('.llm-wiki/domain.json', json.dumps(self.profile))
        pipeline.git(self.f.seed, 'add', '.')
        pipeline.git(self.f.seed, 'commit', '-m', 'newer profile')
        pipeline.git(self.f.seed, 'push', 'origin', 'main')
        result = self.publish_profiled(self.f.clone_publisher())
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_pr_body_includes_decisions_and_unknowns(self):
        """The verified report appears in the actual body-file passed to the PR API."""
        self.build_profiled()
        result = self.publish_profiled(self.f.clone_publisher())
        self.assertEqual(result.returncode, 0, result.stderr)
        body = json.loads(self.f.fake_state.read_text())['prs'][0]['body']
        self.assertIn('Migrate existing screen', body)
        self.assertIn('External configuration not verified.', body)

    def test_rejects_v1_downgrade(self):
        """A profiled artifact cannot use legacy transport to bypass report checks."""
        self.build_profiled()
        value = json.loads(self.f.metadata.read_text())
        value = {key: value[key] for key in ('version', 'changed', 'bytes', 'sha256', 'base_ref', 'seed_tree')}
        value['version'] = 1
        self.f.metadata.write_text(json.dumps(value))
        self.assert_rejected_without_mutation(self.f.clone_publisher())

    def test_rejects_wrong_trusted_base(self):
        """Matching report checksums do not override the separately bound batch base."""
        self.build_profiled()
        self.source_base = self.f.head_sha
        self.assert_rejected_without_mutation(self.f.clone_publisher())
