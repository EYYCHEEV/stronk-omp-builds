import contextlib
import hashlib
import io
import json
import unittest

import support
from common import BINARY_NAME, MANIFEST_NAME, Refusal
from manifest import build_manifest, main, verify_artifact, write_manifest

PASSING = [{"argv": ["bun", "test", "a.test.ts"], "exit_code": 0}]


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.directory = support.TemporaryDirectory()
        self.base = self.directory.__enter__()
        self.addCleanup(self.directory.__exit__, None, None, None)
        self.source = support.make_source(self.base / "src")
        self.out = self.base / "out"
        self.out.mkdir()
        self.binary = self.out / BINARY_NAME
        self.binary.write_bytes(b"\xcf\xfa\xed\xfe fake mach-o")
        self.results = self.base / "results.json"
        self.results.write_text(json.dumps(PASSING), encoding="utf-8")
        self.environment = support.runner_environment()

    def write(self, environment=None):
        return write_manifest(
            self.source, self.binary, self.results, self.out / MANIFEST_NAME, environment or self.environment
        )

    def test_records_every_required_field(self):
        manifest = self.write()
        self.assertEqual(manifest["fork"], {"repository": "EYYCHEEV/oh-my-pi", "tag": support.TAG, "commit": support.COMMIT})
        self.assertEqual(manifest["binary"]["sha256"], hashlib.sha256(self.binary.read_bytes()).hexdigest())
        self.assertEqual(manifest["binary"]["size"], self.binary.stat().st_size)
        self.assertEqual(manifest["omp_version"], "18.14.2")
        self.assertEqual(manifest["toolchain"], {"bun": "1.4.4", "rust_channel": "nightly-2026-10-06"})
        self.assertEqual(manifest["runner"]["image_os"], "macos15")
        self.assertEqual(manifest["runner"]["image_version"], "20261104.0514.1")
        self.assertEqual(manifest["tests"], PASSING)
        self.assertEqual(json.loads((self.out / MANIFEST_NAME).read_text(encoding="utf-8")), manifest)

    def test_refuses_to_write_without_runner_facts(self):
        for name in ("ImageOS", "ImageVersion", "RUNNER_OS", "GITHUB_SHA", "GITHUB_RUN_ID"):
            with self.subTest(name=name):
                environment = {key: value for key, value in self.environment.items() if key != name}
                with self.assertRaises(Refusal):
                    build_manifest(self.source, self.binary, self.results, environment)

    def test_refuses_to_write_for_bad_tag_commit_or_failing_tests(self):
        with self.assertRaises(Refusal):
            build_manifest(self.source, self.binary, self.results, {**self.environment, "TAG": "stronk-omp-x"})
        with self.assertRaises(Refusal):
            build_manifest(self.source, self.binary, self.results, {**self.environment, "COMMIT": "abc"})
        self.results.write_text(json.dumps([{"argv": ["bun", "test", "a"], "exit_code": 1}]), encoding="utf-8")
        with self.assertRaises(Refusal):
            build_manifest(self.source, self.binary, self.results, self.environment)
        self.results.write_text("[]", encoding="utf-8")
        with self.assertRaises(Refusal):
            build_manifest(self.source, self.binary, self.results, self.environment)

    def test_verify_accepts_the_untouched_artifact(self):
        self.write()
        manifest = verify_artifact(self.out, self.environment)
        self.assertEqual(manifest["binary"]["name"], BINARY_NAME)

    def test_verify_refuses_a_changed_binary_of_the_same_size(self):
        self.write()
        original = self.binary.read_bytes()
        self.binary.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
        with self.assertRaises(Refusal) as caught:
            verify_artifact(self.out, self.environment)
        self.assertIn("differs from manifest", str(caught.exception))

    def test_verify_refuses_a_binary_of_another_size(self):
        self.write()
        self.binary.write_bytes(b"tampered")
        with self.assertRaises(Refusal):
            verify_artifact(self.out, self.environment)

    def test_verify_refuses_a_different_tag_or_commit(self):
        self.write()
        with self.assertRaises(Refusal):
            verify_artifact(self.out, {**self.environment, "TAG": "stronk-omp-20261102T000000Z-1"})
        with self.assertRaises(Refusal):
            verify_artifact(self.out, {**self.environment, "COMMIT": support.OTHER_COMMIT})

    def test_verify_refuses_a_different_run(self):
        self.write()
        with self.assertRaises(Refusal):
            verify_artifact(self.out, {**self.environment, "GITHUB_RUN_ID": "1"})
        with self.assertRaises(Refusal):
            verify_artifact(self.out, {**self.environment, "GITHUB_SHA": support.COMMIT})
        with self.assertRaises(Refusal):
            verify_artifact(self.out, {key: value for key, value in self.environment.items() if key != "GITHUB_RUN_ID"})

    def test_verify_refuses_extra_or_missing_files(self):
        self.write()
        (self.out / "extra.txt").write_text("x", encoding="utf-8")
        with self.assertRaises(Refusal):
            verify_artifact(self.out, self.environment)
        (self.out / "extra.txt").unlink()
        (self.out / MANIFEST_NAME).unlink()
        with self.assertRaises(Refusal):
            verify_artifact(self.out, self.environment)

    def test_verify_refuses_a_manifest_that_does_not_record_passing_tests(self):
        manifest = self.write()
        for tests in ([], [{"argv": ["bun", "test", "a"], "exit_code": 1}], "x"):
            with self.subTest(tests=tests):
                (self.out / MANIFEST_NAME).write_text(json.dumps({**manifest, "tests": tests}), encoding="utf-8")
                with self.assertRaises(Refusal):
                    verify_artifact(self.out, self.environment)

    def test_verify_refuses_malformed_manifests(self):
        for content in ("not json", "[]", json.dumps({"schema": 2}), json.dumps({"schema": 1, "fork": []})):
            with self.subTest(content=content):
                (self.out / MANIFEST_NAME).write_text(content, encoding="utf-8")
                with self.assertRaises(Refusal):
                    verify_artifact(self.out, self.environment)

    def test_main_reports_refusal_with_exit_code_two(self):
        self.write()
        self.binary.write_bytes(b"tampered")
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["verify", "--artifact-dir", str(self.out)], self.environment), 2)
            self.assertEqual(
                main(
                    [
                        "write",
                        "--source", str(self.source),
                        "--binary", str(self.binary),
                        "--tests", str(self.results),
                        "--output", str(self.out / "again.json"),
                    ],
                    self.environment,
                ),
                0,
            )


if __name__ == "__main__":
    unittest.main()
