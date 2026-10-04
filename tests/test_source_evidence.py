"""Exercise evidence against actual multi-commit Git ranges."""
import subprocess
import tempfile
import unittest
from pathlib import Path
from scripts.wiki.source_evidence import source_path_is_valid, validate_source_range
from scripts.wiki.topic_contract import ContractError


class SourceEvidenceTests(unittest.TestCase):
    """Use a deletion followed by another commit to distinguish range evidence."""

    def setUp(self):
        """Create a tracked file, delete it, and commit another change afterward."""
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.git('init', '-b', 'main')
        self.git('config', 'user.name', 'Test')
        self.git('config', 'user.email', 'test@example.com')
        (self.repo / 'deleted.py').write_text('x=1\n')
        self.base = self.commit('base')
        (self.repo / 'deleted.py').unlink()
        self.deleted = self.commit('delete')
        (self.repo / 'current.py').write_text('x=2\n')
        self.head = self.commit('later')

    def git(self, *args):
        """Execute Git and return its UTF-8 stdout for fixture setup."""
        return subprocess.run(['git', '-C', str(self.repo), *args], capture_output=True, text=True, encoding='utf-8', check=True).stdout.strip()

    def commit(self, message):
        """Stage a fixture state and return its commit ID."""
        self.git('add', '-A')
        self.git('commit', '-m', message)
        return self.git('rev-parse', 'HEAD')

    def test_accepts_path_deleted_before_last_commit(self):
        """Intermediate deletions stay provable at the batch's final head."""
        self.assertTrue(source_path_is_valid(self.repo, 'deleted.py', self.head, self.base))
        self.assertFalse(source_path_is_valid(self.repo, 'never.py', self.head, self.base))
        self.assertTrue(source_path_is_valid(self.repo, 'current.py', self.head, self.base))

    def test_rejects_deletion_outside_source_range(self):
        """An earlier deletion cannot be attributed to a later batch."""
        self.assertFalse(source_path_is_valid(self.repo, 'deleted.py', self.head, self.deleted))
        self.assertFalse(source_path_is_valid(self.repo, 'deleted.py', self.head))

    def test_rejects_non_ancestor_base(self):
        """Source bounds must point forward through the actual commit graph."""
        with self.assertRaises(ContractError):
            validate_source_range(self.repo, self.head, self.base)

    def test_rejects_unsafe_paths_and_accepts_last_commit_deletion(self):
        """Legacy last-commit deletions work while absolute and traversal paths fail."""
        self.assertTrue(source_path_is_valid(self.repo, 'deleted.py', self.deleted))
        for path in ('../deleted.py', '/deleted.py', 'C:/deleted.py', './current.py', 'a\\b', ''):
            self.assertFalse(source_path_is_valid(self.repo, path, self.head, self.base))
