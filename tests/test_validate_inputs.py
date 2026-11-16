import contextlib
import hashlib
import io
import json
import unittest

import support
from common import Refusal
from validate_inputs import main, validate

TESTS = json.dumps([["bun", "test", "packages/coding-agent/test/a.test.ts"]])
GOOD = {
    "EVENT": "workflow_dispatch",
    "REF": "refs/heads/main",
    "REPOSITORY": "EYYCHEEV/stronk-omp-builds",
    "MODE": "release",
    "TAG": support.TAG,
    "COMMIT": support.COMMIT,
    "TESTS": TESTS,
}
PRECHECK = {
    **{key: value for key, value in GOOD.items() if key != "TAG"},
    "MODE": "precheck",
    "TESTS_SHA256": hashlib.sha256(TESTS.encode("utf-8")).hexdigest(),
    "DISPATCH_ID": "0123456789abcdef0123456789abcdef",
}


class ValidateInputsTests(unittest.TestCase):
    def test_accepts_good_inputs(self):
        self.assertEqual(validate(GOOD), "release")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(GOOD), 0)

    def test_refuses_each_bad_input(self):
        bad = {
            "EVENT": "push",
            "REF": "refs/heads/other",
            "REPOSITORY": "someone/stronk-omp-builds",
            "MODE": "publish",
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

    def test_release_refuses_a_tests_digest(self):
        with self.assertRaises(Refusal):
            validate({**GOOD, "TESTS_SHA256": PRECHECK["TESTS_SHA256"]})

    def test_release_refuses_a_dispatch_id(self):
        with self.assertRaises(Refusal) as caught:
            validate({**GOOD, "DISPATCH_ID": PRECHECK["DISPATCH_ID"]})
        self.assertIn("dispatch_id is for precheck mode only", str(caught.exception))

    def test_precheck_refuses_a_missing_or_malformed_dispatch_id(self):
        without = {key: value for key, value in PRECHECK.items() if key != "DISPATCH_ID"}
        for environ in (
            without,
            {**PRECHECK, "DISPATCH_ID": ""},
            {**PRECHECK, "DISPATCH_ID": "0123456789ABCDEF0123456789ABCDEF"},
            {**PRECHECK, "DISPATCH_ID": "0123456789abcdef"},
            {**PRECHECK, "DISPATCH_ID": "0123456789abcdef0123456789abcdef\n"},
            {**PRECHECK, "DISPATCH_ID": "0123456789abcdef0123456789abcdef 1"},
        ):
            with self.subTest(dispatch_id=environ.get("DISPATCH_ID")):
                with self.assertRaises(Refusal) as caught:
                    validate(environ)
                self.assertIn("dispatch_id", str(caught.exception))

    def test_precheck_accepts_a_commit_and_the_matching_tests_digest_without_a_tag(self):
        self.assertEqual(validate(PRECHECK), "precheck")
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(PRECHECK), 0)
        self.assertIn("precheck", output.getvalue())

    def test_precheck_refuses_a_tag_a_wrong_digest_or_changed_tests(self):
        cases = {
            "tag given": ({**PRECHECK, "TAG": support.TAG}, "takes no tag"),
            "no digest": ({key: value for key, value in PRECHECK.items() if key != "TESTS_SHA256"}, "tests_sha256"),
            "short digest": ({**PRECHECK, "TESTS_SHA256": "abc"}, "tests_sha256"),
            "other digest": ({**PRECHECK, "TESTS_SHA256": "0" * 64}, "but tests_sha256 says"),
            "changed tests": ({**PRECHECK, "TESTS": json.dumps([["bun", "test", "other.test.ts"]])}, "but tests_sha256 says"),
            "no commit": ({key: value for key, value in PRECHECK.items() if key != "COMMIT"}, "commit"),
        }
        for name, (environ, message) in cases.items():
            with self.subTest(case=name):
                with self.assertRaises(Refusal) as caught:
                    validate(environ)
                self.assertIn(message, str(caught.exception))


if __name__ == "__main__":
    unittest.main()
