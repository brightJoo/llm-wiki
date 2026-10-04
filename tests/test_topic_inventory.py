"""Verify legacy retrieval, scoped candidates, and reproducible Wiki snapshots."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.wiki.topic_contract import ContractError
from scripts.wiki.topic_inventory import build_topic_inventory, build_reverse_relations, select_topic_candidates
from scripts.wiki.prepare_context import prepare_context
from test_topic_contract import demo_profile, topic_metadata, metadata_document


class TopicInventoryTests(unittest.TestCase):
    """Retrieve evidence-linked Topics without deciding their semantic equivalence."""

    def setUp(self):
        """Build a real temporary repository and independent domain profile."""
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name)
        self.profile = demo_profile()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.com")
        self.write("src/ranking.py", "VALUE = 1\n")
        self.base = self.commit("baseline")

    def tearDown(self):
        """Release the repository created only for this test."""
        self.temp.cleanup()

    def git(self, *args):
        """Run Git in the test repository and fail on unexpected process errors."""
        return subprocess.check_output(["git", "-C", str(self.repo), *args], text=True, encoding="utf-8").strip()

    def write(self, path, text):
        """Write UTF-8 fixture content under the temporary repository."""
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    def commit(self, message):
        """Stage fixture changes and return the new source snapshot."""
        self.git("add", ".")
        self.git("commit", "-qm", message)
        return self.git("rev-parse", "HEAD")

    def legacy(self, path="docs/wiki/topics/ranking.md"):
        """Create a legacy document with a real source path and snapshot."""
        self.write(path, f"# Ranking\n\n## Sources\n\n- `src/ranking.py` at `{self.base}`\n")

    def test_includes_legacy_topic_in_candidates(self):
        """Source-linked legacy knowledge remains discoverable before conversion."""
        self.legacy()
        inventory = build_topic_inventory(self.repo)
        result = select_topic_candidates(inventory, [{"status": "M", "path": "src/ranking.py"}], self.profile)
        self.assertEqual(result["topics"][0]["path"], "docs/wiki/topics/ranking.md")
        self.assertIsNone(result["topics"][0]["metadata"])

    def test_matches_renamed_previous_source_path(self):
        """A moved source still retrieves a Topic citing its original path."""
        self.legacy()
        changes = [{"status": "R100", "previous_path": "src/ranking.py", "path": "src/new.py"}]
        self.assertEqual(len(select_topic_candidates(build_topic_inventory(self.repo), changes, self.profile)["topics"]), 1)

    def test_does_not_merge_matching_aliases_across_services(self):
        """Matching display vocabulary does not collapse distinct service scopes."""
        first = topic_metadata("shop-a.ranking")
        second = topic_metadata("shop-b.ranking")
        second["scope"]["services"] = ["other-shop"]
        self.write("docs/wiki/topics/a.md", metadata_document(first))
        self.write("docs/wiki/topics/b.md", metadata_document(second))
        result = select_topic_candidates(build_topic_inventory(self.repo), [{"path": "src/ranking.py", "status": "M"}], self.profile)
        self.assertEqual(len(result["topics"]), 2)
        self.assertEqual({item["metadata"]["id"] for item in result["topics"]}, {"shop-a.ranking", "shop-b.ranking"})

    def test_reverse_relations_preserve_edge_type(self):
        """Navigation links and policy dependencies remain distinguishable."""
        meta = topic_metadata()
        meta["relations"] = [{"type": "applies_policy", "target": "visibility"}, {"type": "related_to", "target": "cache"}]
        self.write("docs/wiki/topics/ranking.md", metadata_document(meta))
        reverse = build_reverse_relations(build_topic_inventory(self.repo))
        self.assertEqual(reverse["visibility"], [{"source": "display.screen.ranking", "type": "applies_policy"}])
        self.assertEqual(reverse["cache"][0]["type"], "related_to")

    def test_candidate_limit_reports_truncation(self):
        """A bounded result exposes omitted candidates instead of hiding them."""
        self.legacy("docs/wiki/topics/a.md")
        self.legacy("docs/wiki/topics/b.md")
        inventory = build_topic_inventory(self.repo)
        result = select_topic_candidates(inventory, [{"status": "M", "path": "src/ranking.py"}], self.profile, limit=1)
        self.assertEqual(result["topics"][0]["path"], "docs/wiki/topics/a.md")
        self.assertTrue(result["truncated"])
        self.assertEqual(result, select_topic_candidates(inventory, [{"status": "M", "path": "src/ranking.py"}], self.profile, limit=1))

    def test_reads_seed_inventory_by_git_ref(self):
        """The seed inventory survives working-tree mutations and new files."""
        self.legacy()
        seed = self.commit("wiki")
        self.write("docs/wiki/topics/ranking.md", metadata_document(topic_metadata("new")))
        self.legacy("docs/wiki/topics/extra.md")
        old = build_topic_inventory(self.repo, seed)
        self.assertEqual(len(old), 1)
        self.assertIsNone(old[0]["metadata"])
        self.assertEqual(len(build_topic_inventory(self.repo)), 2)

    def test_no_profile_preserves_existing_context(self):
        """An unconfigured repository retains the legacy context contract."""
        self.write("src/ranking.py", "VALUE = 2\n")
        head = self.commit("change")
        context = prepare_context(self.repo, self.base, head, self.repo / ".runtime", 200, 1_000_000)
        self.assertNotIn("domain_profile", context)
        self.assertNotIn("topic_candidates", context)

    def test_profiled_context_exports_inventory(self):
        """Committed configuration activates scoped retrieval and runtime inventory."""
        self.legacy()
        self.write(".llm-wiki/domain.json", json.dumps(self.profile))
        self.write("src/ranking.py", "VALUE = 2\n")
        head = self.commit("profile and change")
        context = prepare_context(self.repo, self.base, head, self.repo / ".runtime", 200, 1_000_000)
        self.assertEqual(context["domain_profile"]["id"], "display-demo")
        self.assertEqual(context["topic_candidates"][0]["path"], "docs/wiki/topics/ranking.md")
        self.assertTrue(Path(context["topic_inventory_path"]).is_file())

    def test_inventory_rejects_symlinks(self):
        """Git symlink blobs cannot be treated as trusted Topic contents."""
        self.git("update-index", "--add", "--cacheinfo", "120000," + self.git("hash-object", "src/ranking.py") + ",docs/wiki/topics/link.md")
        self.git("commit", "-qm", "symlink fixture")
        with self.assertRaises(ContractError):
            build_topic_inventory(self.repo, "HEAD")
