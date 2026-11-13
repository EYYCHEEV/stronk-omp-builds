import contextlib
import io
import json
import unittest

import support
from common import Refusal
from validate_inputs import main, validate

GOOD = {
    "EVENT": "workflow_dispatch",
    "REF": "refs/heads/main",
    "REPOSITORY": "EYYCHEEV/omp-mac-build",
    "TAG": support.TAG,
    "COMMIT": support.COMMIT,
    "TESTS": json.dumps([["bun", "test", "packages/coding-agent/test/a.test.ts"]]),
}


class ValidateInputsTests(unittest.TestCase):
    def test_accepts_good_inputs(self):
        validate(GOOD)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(GOOD), 0)

    def test_refuses_each_bad_input(self):
        bad = {
            "EVENT": "push",
            "REF": "refs/heads/other",
            "REPOSITORY": "someone/omp-mac-build",
            "TAG": "stronk-omp-20261101T151318Z-9111\n",
            "COMMIT": support.COMMIT.upper(),
            "TESTS": json.dumps([["sh", "-c", "id"]]),
        }
        for name, value in bad.items():
            with self.subTest(name=name):
                with self.assertRaises(Refusal):
                    validate({**GOOD, name: value})
                with contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(main({**GOOD, name: value}), 2)

    def test_refuses_tags_with_a_shell_payload(self):
        for tag in ("stronk-omp-20261101T151318Z-9111; id", "$(id)", "../stronk-omp-20261101T151318Z-9111", "stronk-omp-20261101T151318Z-"):
            with self.subTest(tag=tag):
                with self.assertRaises(Refusal):
                    validate({**GOOD, "TAG": tag})

    def test_refuses_missing_values(self):
        for name in GOOD:
            with self.subTest(name=name):
                with self.assertRaises(Refusal):
                    validate({key: value for key, value in GOOD.items() if key != name})


if __name__ == "__main__":
    unittest.main()
