"""Synthetic compiler transport tests; these do not measure LLM semantic accuracy."""
import json
import os
import unittest
from pathlib import Path
import test_ingest_pipeline as pipeline
import test_profiled_publisher as profiled
from test_topic_contract import topic_metadata, metadata_document
from scripts.wiki.prepare_context import changed_files

CASES = Path(__file__).parent / 'fixtures/wiki_classification/cases.json'


class DomainPipelineTests(unittest.TestCase):
    """Exercise source binding, pending Wiki accumulation and explicit incompleteness."""

    def setUp(self):
        """Reuse only the setup of the existing profiled remote fixture."""
        self.fixture = profiled.ProfiledPublisherTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.f = self.fixture.f

    def test_synthetic_acceptance_cases_are_complete_and_marked_unmeasured(self):
        """All twelve review scenarios have expectations without claiming model scores."""
        value = json.loads(CASES.read_text(encoding='utf-8'))
        self.assertEqual(value['schema_version'], 1)
        self.assertEqual(value['semantic_evaluation'], 'not_run')
        self.assertEqual(len(value['cases']), 12)
        self.assertEqual(len({c['id'] for c in value['cases']}), 12)
        for case in value['cases']:
            self.assertEqual(set(case), {'id', 'description', 'expected_topics', 'forbidden_scope_expansion', 'required_evidence', 'acceptable_alternatives'})
            self.assertTrue(case['required_evidence'])

    def commit_source(self, message):
        """Commit a synthetic source step and advance remote main."""
        pipeline.git(self.f.seed, 'add', '-A')
        pipeline.git(self.f.seed, 'commit', '-m', message)
        sha = pipeline.git(self.f.seed, 'rev-parse', 'HEAD')
        pipeline.git(self.f.seed, 'push', 'origin', 'main')
        return sha

    def compile_and_publish_fake_batch(self):
        """Run C1–C4 through context, fake compiler, report extraction and real publisher."""
        self.fixture.build_profiled()
        first = self.fixture.publish_profiled(self.f.clone_publisher('first'))
        self.assertEqual(first.returncode, 0, first.stderr)
        original_cursor = self.f.head_sha
        self.f.write_seed('src/visibility.py', 'def excluded(product):\n    return product.hidden\n')
        self.f.write_seed('src/ranking.py', 'DAYS = 7\n')
        self.commit_source('synthetic initial policies')
        self.f.write_seed('src/visibility.py', 'def excluded(product):\n    return product.hidden or product.blocked\n')
        self.commit_source('C1 blocking')
        self.f.write_seed('src/ranking.py', 'DAYS = 3\n')
        self.commit_source('C2 temporary three days')
        pipeline.git(self.f.seed, 'mv', 'src/visibility.py', 'src/product_visibility.py')
        self.commit_source('C3 rename policy path')
        self.f.write_seed('src/ranking.py', 'DAYS = 7\n')
        head = self.commit_source('C4 restore seven days')
        self.f.head_sha = self.f.base = head
        self.f.source_key = f'github:{head}'
        self.fixture.source_base = original_cursor
        pipeline.git(self.f.seed, 'fetch', 'origin', 'wiki/pending:refs/remotes/origin/wiki/pending')
        runtime = self.f.root / 'runtime'
        context_result = pipeline.run('python3', str(pipeline.CONTEXT_PREPARER), '--repo', str(self.f.seed), '--base', head, '--head', head,
                                      '--pending-ref', 'refs/remotes/origin/wiki/pending', '--output-dir', str(runtime), cwd=self.f.seed)
        self.assertEqual(context_result.returncode, 0, context_result.stderr)
        context = json.loads((runtime / 'context.json').read_text(encoding='utf-8'))
        self.assertEqual(context['base_sha'], original_cursor)
        self.assertEqual(context['head_sha'], head)
        pipeline.git(self.f.seed, 'add', 'docs/wiki')
        pipeline.git(self.f.seed, 'commit', '-m', 'snapshot pending Wiki')
        seed_ref = pipeline.git(self.f.seed, 'rev-parse', 'HEAD')
        proposals, decisions = {}, []
        definition = [('visibility', 'policy', ['home', 'ranking'], [], 'Excluded products', '숨김·판매 차단 상품 제외', 'src/product_visibility.py'),
                      ('ranking-sold-out', 'policy', ['ranking'], [{'type': 'exception_of', 'target': 'display.visibility'}], 'Ranking sold-out exception', '숨김·판매 차단 제외 후 품절 7일 이내 노출', 'src/ranking.py'),
                      ('home', 'screen', ['home'], [{'type': 'applies_policy', 'target': 'display.visibility'}], 'Home composition', '기본 노출 정책을 사용하는 홈 구성', 'src/product_visibility.py'),
                      ('ranking', 'screen', ['ranking'], [{'type': 'applies_policy', 'target': 'display.ranking-sold-out'}], 'Ranking composition', '랭킹 전용 품절 예외를 사용하는 구성', 'src/ranking.py')]
        change_paths = [c['path'] for c in changed_files(self.f.seed, original_cursor, head)]
        for name, kind, surfaces, relations, question, behavior, source in definition:
            meta = topic_metadata(f'display.{name}', kind)
            meta.update(question=question, scope={'services': ['demo-shop'], 'surfaces': surfaces}, relations=relations)
            links = '\n'.join(f'- [{edge["target"]}]({edge["target"].removeprefix("display.")}.md)' for edge in relations)
            proposals[f'docs/wiki/topics/{name}.md'] = metadata_document(meta) + f'\n## Current behavior\n\n{behavior}\n\n## Verification\n\nSynthetic fixture only. External flags unverified.\n\n## Sources\n\n- `{source}` at `{head}`\n  final source snapshot\n\n## Related topics\n\n{links}\n'
            decisions.append({'question': question, 'topic_id': meta['id'], 'action': 'create', 'candidates': ['display.screen.search'],
                              'rationale': 'Independent policy/composition question; retain pending search knowledge. C2 was reverted at final head.',
                              'scope': meta['scope'], 'covered_changes': change_paths,
                              'evidence': [{'path': source, 'symbol': 'excluded' if name == 'visibility' else 'DAYS / caller scope', 'sha': head}],
                              'checked_dependents': [], 'unknowns': ['Synthetic source does not prove operational flags.']})
        index = (self.f.seed / 'docs/wiki/index.md').read_text(encoding='utf-8')
        proposals['docs/wiki/index.md'] = index + ''.join(f'- [{name}](topics/{name}.md)\n' for name, *_ in definition)
        proposals['docs/wiki/log.md'] = (self.f.seed / 'docs/wiki/log.md').read_text(encoding='utf-8') + f'\n## 2026-10-04T03:00:00Z — `{self.f.source_key}`\n\n- Topics: visibility, ranking-sold-out, home, ranking\n- Drift: Unverified approved intent\n'
        report = {'schema_version': 1, 'source_key': self.f.source_key, 'head_sha': head, 'coverage': 'complete', 'decisions': decisions, 'ignored_changes': []}
        payload = self.f.root / 'fake-proposal.json'
        payload.write_text(json.dumps({'files': proposals, 'report': report}, ensure_ascii=False), encoding='utf-8')
        compiler = self.f.root / 'fake-domain-compiler'
        compiler.write_text('#!/usr/bin/env python3\nimport json, os\nfrom pathlib import Path\nproposal = json.loads(Path(os.environ["WIKI_PROPOSAL"]).read_text(encoding="utf-8"))\nfor name, content in proposal["files"].items():\n    path = Path(name)\n    path.parent.mkdir(parents=True, exist_ok=True)\n    path.write_text(content, encoding="utf-8", newline="\\n")\nprint(json.dumps({"is_error": False, "result": json.dumps(proposal["report"], ensure_ascii=False)}, ensure_ascii=False))\n', encoding='utf-8')
        compiler.chmod(0o755)
        env = os.environ.copy()
        env.update(CLAUDE_BIN=str(compiler), WIKI_PROPOSAL=str(payload))
        result_path = runtime / 'compiler.json'
        compiled = pipeline.run(str(pipeline.RUN_COMPILER), str(pipeline.PROJECT_ROOT / 'CLAUDE.md'), str(pipeline.PROJECT_ROOT / '.llm-wiki/prompts/ingest.md'), str(result_path), cwd=self.f.seed, env=env)
        self.assertEqual(compiled.returncode, 0, compiled.stderr)
        extracted = pipeline.run('python3', str(pipeline.PROJECT_ROOT / 'scripts/wiki/classification_report.py'), '--repo', str(self.f.seed), '--source-base-ref', original_cursor, '--source-key', self.f.source_key,
                                 '--wiki-base-ref', seed_ref, '--compiler-result', str(result_path), '--output', str(self.fixture.report_path), cwd=self.f.seed)
        self.assertEqual(extracted.returncode, 0, extracted.stderr)
        created = pipeline.run(str(pipeline.CREATE_PATCH), seed_ref, str(self.f.patch), str(self.f.metadata), head, original_cursor, str(self.fixture.report_path), cwd=self.f.seed)
        self.assertEqual(created.returncode, 0, created.stderr)
        published = self.fixture.publish_profiled(self.f.clone_publisher('second'))
        self.assertEqual(published.returncode, 0, published.stderr)
        pipeline.git(self.f.seed, 'fetch', 'origin', '+wiki/pending:refs/remotes/origin/wiki/pending')
        message = pipeline.git(self.f.seed, 'log', '-1', '--format=%B', 'refs/remotes/origin/wiki/pending')
        state = json.loads(self.f.fake_state.read_text())
        wiki = {name: pipeline.git(self.f.seed, 'show', f'refs/remotes/origin/wiki/pending:docs/wiki/topics/{name}.md') for name in ('ranking-sold-out', 'search')}
        return {'report': json.loads(self.fixture.report_path.read_text(encoding='utf-8')), 'head_sha': head, 'managed_cursor': message.split('LLM-Wiki-Source: github:')[1].strip(),
                'pending_pr_count': len(state['prs']), 'wiki': wiki, 'calls': state['calls']}

    def test_profiled_batch_pipeline_uses_final_head_and_accumulates_pending_wiki(self):
        """Fake C1–C4 output binds the final head and accumulates one pending PR."""
        result = self.compile_and_publish_fake_batch()
        self.assertEqual(result['report']['head_sha'], result['head_sha'])
        self.assertEqual(result['managed_cursor'], result['head_sha'])
        self.assertEqual(result['pending_pr_count'], 1)
        self.assertEqual(result['calls'], ['create', 'edit'])
        self.assertIn('7일', result['wiki']['ranking-sold-out'])
        self.assertNotIn('3일', result['wiki']['ranking-sold-out'])
        self.assertIn('Current synthetic result.', result['wiki']['search'])

    def test_profiled_no_durable_change_requires_complete_report(self):
        """Empty Wiki output still requires verified input accounting before cursor writes."""
        self.fixture.build_profiled(False, 'incomplete')
        self.fixture.assert_rejected_without_mutation(self.f.clone_publisher('incomplete'))
        self.fixture.build_profiled(False, 'complete')
        result = self.fixture.publish_profiled(self.f.clone_publisher('complete'))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(self.f.fake_state.read_text())['calls'], [])

    def test_profile_change_is_not_trusted_from_compiler_worktree(self):
        """Compiler-created scope declarations cannot authorize new report scope IDs."""
        report = self.fixture.build_profiled()
        modified = self.fixture.profile
        modified['scope_dimensions']['surfaces']['values'].append('invented')
        self.f.write_seed('.llm-wiki/domain.json', json.dumps(modified))
        meta = topic_metadata('display.screen.search')
        meta['question'] = report['decisions'][0]['question']
        meta['scope']['surfaces'] = ['invented']
        self.f.write_seed('docs/wiki/topics/search.md', metadata_document(meta))
        report['decisions'][0]['scope'] = meta['scope']
        compiler_result = self.f.root / 'tampered-result.json'
        compiler_result.write_text(json.dumps({'structured_output': report}), encoding='utf-8')
        output = self.f.root / 'untrusted-report.json'
        result = pipeline.run('python3', str(pipeline.PROJECT_ROOT / 'scripts/wiki/classification_report.py'), '--repo', str(self.f.seed), '--source-base-ref', self.fixture.source_base,
                              '--source-key', self.f.source_key, '--compiler-result', str(compiler_result), '--output', str(output), cwd=self.f.seed)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('invalid_scope', result.stderr)
        self.assertFalse(output.exists())
