import io
import tokenize
import unittest

import support


class NoCommentsTests(unittest.TestCase):
    def test_python_sources_hold_no_comments(self):
        files = sorted(
            path
            for folder in ("scripts", "checks", "tests")
            for path in (support.ROOT / folder).glob("*.py")
        )
        self.assertTrue(files)
        for path in files:
            with self.subTest(path=path.name):
                text = path.read_text(encoding="utf-8")
                comments = [
                    token.string
                    for token in tokenize.generate_tokens(io.StringIO(text).readline)
                    if token.type == tokenize.COMMENT
                ]
                self.assertEqual(comments, [])


if __name__ == "__main__":
    unittest.main()
