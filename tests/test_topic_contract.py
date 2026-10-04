"""Exercise domain and Topic contracts with independent JSON fixtures."""

import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.wiki.topic_contract import (
    ContractError, parse_domain_profile, parse_topic_metadata,
    read_domain_profile, validate_topic_metadata,
)


def demo_profile():
    """Return a fresh synthetic profile, unrelated to company policies."""
    return {
        "schema_version": 1, "id": "display-demo",
        "topic_types": ["screen", "module", "policy", "mechanism"],
        "scope_dimensions": {
            "services": {"required": True, "values": ["demo-shop", "other-shop"]},
            "surfaces": {"required": True, "values": ["home", "ranking", "brand"]},
        },
        "aliases": {"surfaces": {"ranking": ["랭킹", "ranking"]}},
        "relation_rules": {
            "applies_policy": {"from": ["screen", "module"], "to": ["policy"]},
            "uses_module": {"from": ["screen", "module"], "to": ["module"]},
            "exception_of": {"from": ["policy"], "to": ["policy"]},
            "depends_on": {"from": ["screen", "module", "policy", "mechanism"], "to": ["screen", "module", "policy", "mechanism"]},
            "related_to": {"from": ["screen", "module", "policy", "mechanism"], "to": ["screen", "module", "policy", "mechanism"]},
        },
    }


def topic_metadata(topic_id="display.screen.ranking", topic_type="screen"):
    """Return valid metadata with explicit, narrow demo scope."""
    return {"schema_version": 1, "id": topic_id, "type": topic_type,
            "question": "랭킹의 후보 판단은?", "scope": {"services": ["demo-shop"], "surfaces": ["ranking"]},
            "lifecycle": "active", "relations": []}


def metadata_document(metadata):
    """Render one real metadata section for parser tests."""
    return "# Ranking\n\n## Topic metadata\n\n```json\n" + json.dumps(metadata, ensure_ascii=False) + "\n```\n\n## Scope\n\nDemo.\n"


class TopicContractTests(unittest.TestCase):
    """Reject ambiguous metadata and retain compatible legacy documents."""

    def setUp(self):
        """Use independent profile values in each test."""
        self.profile = parse_domain_profile(json.dumps(demo_profile()), "profile.json")

    def test_valid_profile_and_metadata(self):
        """A scoped Topic is accepted without modifying its values."""
        meta = parse_topic_metadata(metadata_document(topic_metadata()), "ranking.md")
        self.assertEqual(validate_topic_metadata(meta, self.profile, "ranking.md"), [])
        self.assertEqual(meta["scope"]["services"], ["demo-shop"])

    def test_rejects_duplicate_keys_and_bool_version(self):
        """Duplicate JSON keys and boolean versions cannot bypass the schema."""
        with self.assertRaises(ContractError):
            parse_domain_profile('{"schema_version":1,"schema_version":2}', "profile.json")
        for value in (True, 2, 1.0):
            profile = demo_profile()
            profile["schema_version"] = value
            with self.subTest(version=value), self.assertRaises(ContractError):
                parse_domain_profile(json.dumps(profile), "profile.json")

    def test_ignores_heading_inside_code_fence(self):
        """A quoted example cannot masquerade as a document's metadata."""
        quoted = "````markdown\n" + metadata_document(topic_metadata()) + "````\n"
        self.assertIsNone(parse_topic_metadata(quoted, "example.md"))
        self.assertEqual(parse_topic_metadata(quoted + metadata_document(topic_metadata()), "example.md")["id"], "display.screen.ranking")

    def test_rejects_multiple_metadata_blocks(self):
        """Repeated headings or JSON blocks do not select an arbitrary schema."""
        doc = metadata_document(topic_metadata())
        with self.assertRaises(ContractError):
            parse_topic_metadata(doc + doc, "ranking.md")
        with self.assertRaises(ContractError):
            parse_topic_metadata("## Topic metadata\n\n```json\n{}\n```\n```json\n{}\n```\n", "ranking.md")

    def test_legacy_topic_without_metadata(self):
        """A legacy document is distinct from a malformed metadata section."""
        self.assertIsNone(parse_topic_metadata("# Legacy\n\n## Scope\n\nSearch.\n", "legacy.md"))
        with self.assertRaises(ContractError):
            parse_topic_metadata("## Topic metadata\n\nnot json\n", "broken.md")

    def test_rejects_unknown_scope_and_empty_array(self):
        """Undeclared and empty scopes cannot imply broad applicability."""
        for value in ([], ["unknown"], "ranking", ["ranking", "ranking"]):
            meta = topic_metadata()
            meta["scope"]["surfaces"] = value
            with self.subTest(scope=value):
                self.assertTrue(validate_topic_metadata(meta, self.profile, "ranking.md"))

    def test_accepts_explicit_null_scope(self):
        """An unknown scope remains null instead of becoming a wildcard."""
        meta = topic_metadata()
        meta["scope"]["surfaces"] = None
        self.assertEqual(validate_topic_metadata(meta, self.profile, "ranking.md"), [])
        self.assertIsNone(meta["scope"]["surfaces"])

    def test_rejects_missing_required_fields_and_retired_is_valid(self):
        """Missing fields and invalid lifecycle are errors; retirement is valid."""
        for field in topic_metadata():
            meta = topic_metadata()
            del meta[field]
            with self.subTest(field=field):
                self.assertTrue(validate_topic_metadata(meta, self.profile, "ranking.md"))
        meta = topic_metadata()
        meta["lifecycle"] = "retired"
        self.assertEqual(validate_topic_metadata(meta, self.profile, "ranking.md"), [])

    def test_profile_rejects_unknown_relation_type_and_bad_alias(self):
        """Profile aliases and relationship endpoints refer to declared values."""
        for mutate in ("alias", "relation"):
            profile = copy.deepcopy(demo_profile())
            if mutate == "alias":
                profile["aliases"]["surfaces"]["missing"] = ["same"]
            else:
                profile["relation_rules"]["exception_of"]["to"] = ["missing"]
            with self.subTest(case=mutate), self.assertRaises(ContractError):
                parse_domain_profile(json.dumps(profile), "profile.json")

    def test_profile_is_read_from_source_commit(self):
        """Compiler changes to the working profile do not change trusted input."""
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
            path = repo / ".llm-wiki/domain.json"
            path.parent.mkdir()
            path.write_text(json.dumps(demo_profile()), encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
            subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "profile"], check=True)
            sha = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
            path.write_text('{"bad":true}', encoding="utf-8")
            self.assertEqual(read_domain_profile(repo, sha)["id"], "display-demo")
            path.unlink()
            subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
            subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "remove profile"], check=True)
            missing_sha = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
            self.assertIsNone(read_domain_profile(repo, missing_sha))

