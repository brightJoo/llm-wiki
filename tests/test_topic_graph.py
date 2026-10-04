"""Validate declared relationships and gradual legacy migration."""
import copy
import unittest
from scripts.wiki.topic_contract import validate_topic_graph
from test_topic_contract import demo_profile, topic_metadata


def record(topic_id='screen.ranking', kind='screen', relations=None, links=None):
    """Construct an inventory record with valid metadata and optional edges."""
    meta = topic_metadata(topic_id, kind)
    meta['relations'] = relations or []
    return {'path': f'docs/wiki/topics/{topic_id}.md', 'metadata': meta, 'links': links or []}


class TopicGraphTests(unittest.TestCase):
    """Check graph structure without making semantic policy claims."""

    def codes(self, records, changed=None, base=None):
        """Return issue codes for a graph with declared fixture scope."""
        return {i.code for i in validate_topic_graph(records, demo_profile(), changed or set(), base or [])}

    def test_rejects_duplicate_topic_id(self):
        """Separate paths may not silently reuse the same stable Topic ID."""
        a, b = record(), record()
        b['path'] = 'docs/wiki/topics/other.md'
        self.assertIn('duplicate_topic_id', self.codes([a, b]))

    def test_rejects_missing_or_wrong_type_relation(self):
        """Every declared target must exist and match the profile's endpoint types."""
        a = record(relations=[{'type': 'applies_policy', 'target': 'missing'}])
        self.assertIn('missing_relation_target', self.codes([a]))
        a['metadata']['relations'][0]['target'] = 'other'
        self.assertIn('wrong_relation_type', self.codes([a, record('other', 'screen')]))

    def test_requires_relation_body_link(self):
        """An edge in metadata also needs a real Markdown navigation link."""
        a = record(relations=[{'type': 'applies_policy', 'target': 'policy'}])
        b = record('policy', 'policy')
        self.assertIn('missing_relation_link', self.codes([a, b]))
        a['links'] = [b['path']]
        self.assertEqual(self.codes([a, b]), set())

    def test_rejects_exception_cycle(self):
        """Exception inheritance must be acyclic, including self references."""
        a = record('a', 'policy', [{'type': 'exception_of', 'target': 'b'}], ['docs/wiki/topics/b.md'])
        b = record('b', 'policy', [{'type': 'exception_of', 'target': 'a'}], [a['path']])
        self.assertIn('exception_cycle', self.codes([a, b]))
        a['metadata']['relations'][0]['target'] = 'a'
        self.assertIn('exception_cycle', self.codes([a]))

    def test_preserves_untouched_legacy_topic(self):
        """Unrelated legacy documents do not require a whole-Wiki migration."""
        legacy = {'path': 'docs/wiki/topics/legacy.md', 'metadata': None, 'links': []}
        self.assertEqual(self.codes([legacy], base=[legacy]), set())
        self.assertIn('missing_topic_metadata', self.codes([legacy], {legacy['path']}, [legacy]))

    def test_rejects_changed_existing_id_and_topic_deletion(self):
        """Updates preserve identity and retire documents rather than deleting paths."""
        old = record()
        changed = copy.deepcopy(old)
        changed['metadata']['id'] = 'renamed'
        self.assertIn('changed_topic_id', self.codes([changed], {old['path']}, [old]))
        self.assertIn('deleted_topic', self.codes([], {old['path']}, [old]))
        changed['metadata'] = copy.deepcopy(old['metadata'])
        changed['metadata']['lifecycle'] = 'retired'
        self.assertEqual(self.codes([changed], {old['path']}, [old]), set())
