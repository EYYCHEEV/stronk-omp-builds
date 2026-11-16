import contextlib
import hashlib
import io
import json
import unittest

import support
from common import BINARY_NAME, MANIFEST_NAME, Refusal
from manifest import build_manifest, main, verify_artifact, write_manifest, write_precheck_record

PASSING = [{"argv": ["bun", "test", "a.test.ts"], "exit_code": 0}]
MEASURED = {
    "sqlite_version": "3.50.1",
    "bun_version": "1.4.4",
    "bun_revision": "0123456789abcdef0123456789abcdef01234567",
    "bun_sha256": "a" * 64,
}
DISPATCH_ID = "0123456789abcdef0123456789abcdef"


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
        self.probe = self.base / "runtime-probe.json"
        self.probe.write_text(json.dumps(MEASURED), encoding="utf-8")
        self.environment = support.runner_environment()

    def write(self, environment=None):
        return write_manifest(
            self.source, self.binary, self.results, self.probe, self.out / MANIFEST_NAME, environment or self.environment
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
        self.assertEqual(manifest["measured"], MEASURED)
        self.assertEqual(json.loads((self.out / MANIFEST_NAME).read_text(encoding="utf-8")), manifest)

    def test_the_measured_block_comes_only_from_the_probe_file(self):
        manifest = build_manifest(
            self.source, self.binary, self.results, self.probe,
            {**self.environment, "ImageOS": "macos14", "BUN_VERSION": "9.9.9", "SQLITE_VERSION": "3.0.0"},
        )
        self.assertEqual(manifest["measured"], MEASURED)
        self.assertNotEqual(manifest["measured"]["sqlite_version"], "3.43.2")

    def test_refuses_to_write_without_a_valid_probe(self):
        for content in (None, "not json", json.dumps({**MEASURED, "sqlite_version": "latest"}), json.dumps({"bun_version": "1.4.4"})):
            with self.subTest(content=content):
                if content is None:
                    self.probe.unlink(missing_ok=True)
                else:
                    self.probe.write_text(content, encoding="utf-8")
                with self.assertRaises(Refusal):
                    build_manifest(self.source, self.binary, self.results, self.probe, self.environment)

    def test_refuses_to_write_without_runner_facts(self):
        for name in ("ImageOS", "ImageVersion", "RUNNER_OS", "GITHUB_SHA", "GITHUB_RUN_ID"):
            with self.subTest(name=name):
                environment = {key: value for key, value in self.environment.items() if key != name}
                with self.assertRaises(Refusal):
                    build_manifest(self.source, self.binary, self.results, self.probe, environment)

    def test_refuses_to_write_for_bad_tag_commit_or_failing_tests(self):
        with self.assertRaises(Refusal):
            build_manifest(self.source, self.binary, self.results, self.probe, {**self.environment, "TAG": "stronk-omp-x"})
        with self.assertRaises(Refusal):
            build_manifest(self.source, self.binary, self.results, self.probe, {**self.environment, "COMMIT": "abc"})
        self.results.write_text(json.dumps([{"argv": ["bun", "test", "a"], "exit_code": 1}]), encoding="utf-8")
        with self.assertRaises(Refusal):
            build_manifest(self.source, self.binary, self.results, self.probe, self.environment)
        self.results.write_text("[]", encoding="utf-8")
        with self.assertRaises(Refusal):
            build_manifest(self.source, self.binary, self.results, self.probe, self.environment)

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

    def test_verify_refuses_a_manifest_without_a_valid_measured_block(self):
        manifest = self.write()
        without = {key: value for key, value in manifest.items() if key != "measured"}
        for content in (
            without,
            {**manifest, "measured": "3.43.2"},
            {**manifest, "measured": {**MEASURED, "sqlite_version": "3"}},
            {**manifest, "measured": {**MEASURED, "extra": "x"}},
        ):
            with self.subTest(content=content.get("measured")):
                (self.out / MANIFEST_NAME).write_text(json.dumps(content), encoding="utf-8")
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
                        "--probe", str(self.probe),
                        "--output", str(self.out / "again.json"),
                    ],
                    self.environment,
                ),
                0,
            )

    def precheck_environment(self):
        environment = {key: value for key, value in self.environment.items() if key != "TAG"}
        return {**environment, "DISPATCH_ID": DISPATCH_ID}

    def test_precheck_record_holds_the_commit_measurement_runner_builder_and_tests_but_no_tag(self):
        output = self.base / "precheck.json"
        record = write_precheck_record(self.results, self.probe, output, self.precheck_environment())
        self.assertEqual(record["mode"], "precheck")
        self.assertEqual(record["dispatch_id"], DISPATCH_ID)
        self.assertEqual(record["fork"], {"repository": "EYYCHEEV/oh-my-pi", "commit": support.COMMIT})
        self.assertEqual(record["measured"], MEASURED)
        self.assertEqual(record["runner"]["image_os"], "macos15")
        self.assertEqual(record["builder"]["run_id"], "1234567890")
        self.assertEqual(record["tests"], PASSING)
        self.assertNotIn("binary", record)
        self.assertEqual(json.loads(output.read_text(encoding="utf-8")), record)

    def test_precheck_record_refuses_a_tag_a_bad_probe_failing_tests_or_an_existing_file(self):
        output = self.base / "precheck.json"
        with self.assertRaises(Refusal):
            write_precheck_record(self.results, self.probe, output, self.environment)
        self.probe.write_text("{}", encoding="utf-8")
        with self.assertRaises(Refusal):
            write_precheck_record(self.results, self.probe, output, self.precheck_environment())
        self.probe.write_text(json.dumps(MEASURED), encoding="utf-8")
        self.results.write_text(json.dumps([{"argv": ["bun", "test", "a"], "exit_code": 1}]), encoding="utf-8")
        with self.assertRaises(Refusal):
            write_precheck_record(self.results, self.probe, output, self.precheck_environment())
        self.assertFalse(output.exists())
        self.results.write_text(json.dumps(PASSING), encoding="utf-8")
        for dispatch_id in (None, "", DISPATCH_ID.upper(), DISPATCH_ID[:-1]):
            environment = {key: value for key, value in self.precheck_environment().items() if key != "DISPATCH_ID"}
            if dispatch_id is not None:
                environment["DISPATCH_ID"] = dispatch_id
            with self.subTest(dispatch_id=dispatch_id), self.assertRaises(Refusal) as caught:
                write_precheck_record(self.results, self.probe, output, environment)
            self.assertIn("dispatch_id", str(caught.exception))
        self.assertFalse(output.exists())
        output.write_text("taken", encoding="utf-8")
        arguments = ["precheck", "--tests", str(self.results), "--probe", str(self.probe), "--output", str(output)]
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(arguments, self.precheck_environment()), 2)
            output.unlink()
            self.assertEqual(main(arguments, self.precheck_environment()), 0)
        self.assertEqual(output.read_text(encoding="utf-8").count('"mode": "precheck"'), 1)


if __name__ == "__main__":
    unittest.main()
