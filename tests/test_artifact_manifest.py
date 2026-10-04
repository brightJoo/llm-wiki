"""Check exact v1 compatibility and byte-bound classification artifacts."""
import hashlib
import unittest
from scripts.wiki.artifact_manifest import build_manifest, validate_manifest
from scripts.wiki.topic_contract import ContractError


class ArtifactManifestTests(unittest.TestCase):
    """Pure checks distinguish transport integrity from semantic report validation."""

    def test_v1_manifest_remains_compatible(self):
        """Unprofiled callers retain the existing exact six-field schema."""
        manifest = build_manifest(b'patch', 'b' * 40, 'absent')
        self.assertEqual(set(manifest), {'version', 'changed', 'bytes', 'sha256', 'base_ref', 'seed_tree'})
        self.assertEqual(validate_manifest(manifest, b'patch'), manifest)

    def test_v2_binds_classification_checksum_and_source_range(self):
        """Even empty patches bind the exact classification bytes and source bounds."""
        report = b'{"coverage":"complete"}\n'
        manifest = build_manifest(b'', 'b' * 40, 'absent', 'a' * 40, 'b' * 40, 'ready', report)
        self.assertEqual(manifest['version'], 2)
        self.assertEqual(manifest['classification_sha256'], hashlib.sha256(report).hexdigest())
        self.assertEqual(validate_manifest(manifest, b'', report), manifest)
        with self.assertRaises(ContractError):
            validate_manifest(manifest, b'', b'changed')

    def test_rejects_incomplete_or_ambiguous_transport_fields(self):
        """Bool versions/counts, extra fields, missing reports and wrong patches fail."""
        for mutation in ({'version': True}, {'bytes': True}, {'extra': 1}, {'sha256': 'a' * 64}):
            value = build_manifest(b'patch', 'HEAD', 'absent')
            value.update(mutation)
            with self.assertRaises(ContractError):
                validate_manifest(value, b'patch')
        with self.assertRaises(ContractError):
            build_manifest(b'', 'b' * 40, 'absent', 'a' * 40, 'b' * 40, 'ready')

    def test_skipped_ranges_require_empty_patch_and_no_report(self):
        """A skipped batch cannot smuggle document changes or unverified report data."""
        value = build_manifest(b'', 'b' * 40, 'absent', 'a' * 40, 'b' * 40, 'docs_wiki_only')
        self.assertIsNone(value['classification_sha256'])
        self.assertEqual(validate_manifest(value, b''), value)
        for patch, report in ((b'patch', None), (b'', b'{}')):
            with self.assertRaises(ContractError):
                build_manifest(patch, 'b' * 40, 'absent', 'a' * 40, 'b' * 40, 'no_changes', report)
