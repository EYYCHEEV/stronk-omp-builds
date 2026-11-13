import unittest

import support
from common import Refusal
from verify_checkout import verify

TAG = support.TAG


class VerifyCheckoutTests(unittest.TestCase):
    def setUp(self):
        self.directory = support.TemporaryDirectory()
        self.base = self.directory.__enter__()
        self.addCleanup(self.directory.__exit__, None, None, None)
        self.repo = self.base / "src"
        self.repo.mkdir()
        support.git(self.repo, "init", "-q", "-b", "main")
        (self.repo / "f").write_text("1", encoding="utf-8")
        support.git(self.repo, "add", "f")
        support.git(self.repo, "commit", "-q", "-m", "first")
        self.first = support.git(self.repo, "rev-parse", "HEAD")
        (self.repo / "f").write_text("2", encoding="utf-8")
        support.git(self.repo, "commit", "-q", "-am", "second")
        self.second = support.git(self.repo, "rev-parse", "HEAD")

    def test_accepts_a_lightweight_tag_on_head(self):
        support.git(self.repo, "tag", TAG)
        verify(self.repo, TAG, self.second)

    def test_accepts_an_annotated_tag_on_head(self):
        support.git(self.repo, "tag", "-a", "-m", "release", TAG)
        verify(self.repo, TAG, self.second)

    def test_refuses_a_commit_that_is_not_head(self):
        support.git(self.repo, "tag", TAG)
        with self.assertRaises(Refusal):
            verify(self.repo, TAG, self.first)

    def test_refuses_a_tag_that_points_elsewhere(self):
        support.git(self.repo, "tag", TAG, self.first)
        with self.assertRaises(Refusal) as caught:
            verify(self.repo, TAG, self.second)
        self.assertIn("points at", str(caught.exception))

    def test_refuses_a_missing_tag(self):
        with self.assertRaises(Refusal):
            verify(self.repo, TAG, self.second)

    def test_refuses_malformed_tag_or_commit(self):
        support.git(self.repo, "tag", TAG)
        with self.assertRaises(Refusal):
            verify(self.repo, "v1.0.0", self.second)
        with self.assertRaises(Refusal):
            verify(self.repo, TAG, self.second.upper())
        with self.assertRaises(Refusal):
            verify(self.repo, TAG, self.second[:12])


if __name__ == "__main__":
    unittest.main()
