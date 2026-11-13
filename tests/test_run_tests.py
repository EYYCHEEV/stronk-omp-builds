import contextlib
import io
import json
import os
import unittest
from pathlib import Path
from unittest import mock

import support
from common import Refusal
from run_tests import main, parse_tests


def refused(test, raw):
    with test.assertRaises(Refusal):
        parse_tests(raw)


class ParseTests(unittest.TestCase):
    def test_accepts_bun_and_cargo_forms(self):
        entries = parse_tests(
            json.dumps(
                [
                    ["bun", "test", "packages/coding-agent/test/a.test.ts", "--bail", "--timeout=60000"],
                    ["bun", "test", ".omp/tests/b.test.ts"],
                    ["cargo", "test", "-p", "pi-natives", "--locked", "--", "--nocapture", "--test-threads=1", "some::name"],
                    ["cargo", "test", "--workspace", "--no-fail-fast"],
                    ["cargo", "test", "--manifest-path", "crates/x/Cargo.toml", "--package=pi-natives"],
                ]
            )
        )
        self.assertEqual(len(entries), 5)
        self.assertEqual(entries[0].paths, ["packages/coding-agent/test/a.test.ts"])
        self.assertEqual(entries[4].manifests, ["crates/x/Cargo.toml"])

    def test_refuses_shell_strings(self):
        refused(self, json.dumps("bun test a.test.ts"))
        refused(self, json.dumps(["bun test a.test.ts"]))
        refused(self, json.dumps([["bun test a.test.ts"]]))
        refused(self, json.dumps([["bun", "test a.test.ts"]]))
        refused(self, json.dumps([["sh", "-c", "bun test a.test.ts"]]))

    def test_refuses_other_programs_and_verbs(self):
        for entry in (
            ["python3", "test", "a.py"],
            ["node", "test", "a.js"],
            ["/usr/bin/bun", "test", "a.test.ts"],
            ["./bun", "test", "a.test.ts"],
            ["bunx", "test", "a.test.ts"],
            ["bun", "run", "a.ts"],
            ["bun", "x", "a"],
            ["bun", "build", "a.ts"],
            ["cargo", "build"],
            ["cargo", "run"],
            ["cargo", "+nightly", "test"],
            ["cargo", "install", "x"],
            ["env", "bun", "test", "a.test.ts"],
            ["bun", "test"],
        ):
            with self.subTest(entry=entry):
                refused(self, json.dumps([entry]))

    def test_refuses_paths_that_leave_the_repository(self):
        for path in ("../a.test.ts", "/etc/passwd", "a/../../b.test.ts", "a//b.test.ts", "..", "a/..", "~/x", "$HOME/x"):
            with self.subTest(path=path):
                refused(self, json.dumps([["bun", "test", path]]))
        refused(self, json.dumps([["cargo", "test", "--manifest-path", "../Cargo.toml"]]))
        refused(self, json.dumps([["cargo", "test", "--manifest-path=/abs/Cargo.toml"]]))

    def test_refuses_options_that_run_code(self):
        for entry in (
            ["bun", "test", "--preload", "evil.ts", "a.test.ts"],
            ["bun", "test", "--preload=evil.ts", "a.test.ts"],
            ["bun", "test", "-r", "evil.ts", "a.test.ts"],
            ["bun", "test", "--require", "evil.js", "a.test.ts"],
            ["bun", "test", "--import", "evil.js", "a.test.ts"],
            ["bun", "test", "--config=evil.toml", "a.test.ts"],
            ["bun", "test", "-c", "evil.toml", "a.test.ts"],
            ["bun", "test", "--bunfig=evil.toml", "a.test.ts"],
            ["bun", "test", "--env-file=.env", "a.test.ts"],
            ["bun", "test", "--cwd=/tmp", "a.test.ts"],
            ["bun", "test", "--eval", "x", "a.test.ts"],
            ["bun", "test", "--timeout=abc", "a.test.ts"],
            ["bun", "test", "--", "a.test.ts"],
            ["cargo", "test", "--config", "build.rustc-wrapper='sh'"],
            ["cargo", "test", "--config=build.rustc-wrapper=\"sh\""],
            ["cargo", "test", "-Zunstable-options"],
            ["cargo", "test", "-Z", "build-std"],
            ["cargo", "test", "--target-dir", "/tmp/x"],
            ["cargo", "test", "--target", "x86_64-unknown-linux-gnu"],
            ["cargo", "test", "--features", "evil"],
            ["cargo", "test", "--message-format=json"],
            ["cargo", "test", "--", "--format=json"],
            ["cargo", "test", "--", "-Zunstable-options"],
            ["cargo", "test", "--", "--exec", "sh"],
            ["cargo", "test", "-p"],
            ["cargo", "test", "-p", "x;y"],
            ["cargo", "test", "--package=x y"],
        ):
            with self.subTest(entry=entry):
                refused(self, json.dumps([entry]))

    def test_refuses_shell_metacharacters_and_control_characters(self):
        for argument in ("a.test.ts;rm", "a.test.ts|cat", "$(id)", "`id`", "a b.test.ts", "a\n.test.ts", "a\x00.test.ts", "ä.test.ts", "a&b", "a>b"):
            with self.subTest(argument=argument):
                refused(self, json.dumps([["bun", "test", argument]]))
        refused(self, json.dumps([["cargo", "test", "x;y"]]))
        refused(self, json.dumps([["cargo", "test", "$(id)"]]))

    def test_refuses_bad_json_and_limits(self):
        refused(self, None)
        refused(self, "")
        refused(self, "not json")
        refused(self, "[]")
        refused(self, "{}")
        refused(self, "[[]]")
        refused(self, "[[1,2]]")
        refused(self, "[" * 5000 + "]" * 5000)
        refused(self, json.dumps([["bun", "test", "a.test.ts"]] * 33))
        refused(self, json.dumps([["bun", "test"] + ["a.test.ts"] * 70]))
        refused(self, json.dumps([["bun", "test", "a" * 300]]))
        refused(self, json.dumps([["bun", "test", "a.test.ts"]]) + " " * 17000)


class RunTests(unittest.TestCase):
    def setUp(self):
        self.directory = support.TemporaryDirectory()
        self.base = self.directory.__enter__()
        self.addCleanup(self.directory.__exit__, None, None, None)
        self.repo = self.base / "repo"
        (self.repo / "pkg").mkdir(parents=True)
        (self.repo / "crate").mkdir()
        (self.repo / "a.test.ts").write_text("", encoding="utf-8")
        (self.repo / "pkg" / "b.test.ts").write_text("", encoding="utf-8")
        (self.repo / "crate" / "Cargo.toml").write_text("", encoding="utf-8")
        self.bin = self.base / "bin"
        self.log = self.base / "stub.log"
        self.results = self.base / "results.json"

    def stubs(self, exit_code=0):
        body = f"printf '%s\\n' \"$@\" >> '{self.log}'\npwd >> '{self.log}'\nexit {exit_code}"
        support.make_stub(self.bin, "bun", body)
        support.make_stub(self.bin, "cargo", body)

    def run_main(self, tests, path=None, timeout=60):
        environ = {"TESTS": tests}
        path = f"{self.bin}:/usr/bin:/bin" if path is None else path
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, {"PATH": path}), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(
                ["--repo", str(self.repo), "--results", str(self.results), "--timeout-seconds", str(timeout)],
                environ,
            )
        return code, stdout.getvalue(), stderr.getvalue()

    def test_runs_each_entry_without_a_shell_in_the_repository(self):
        self.stubs()
        tests = json.dumps(
            [
                ["bun", "test", "a.test.ts", "pkg/b.test.ts", "--bail"],
                ["cargo", "test", "-p", "demo", "--", "--nocapture"],
                ["cargo", "test", "--manifest-path", "crate/Cargo.toml"],
            ]
        )
        code, _, _ = self.run_main(tests)
        self.assertEqual(code, 0)
        lines = self.log.read_text(encoding="utf-8").splitlines()
        self.assertEqual(
            lines,
            [
                "test", "a.test.ts", "pkg/b.test.ts", "--bail", str(self.repo),
                "test", "-p", "demo", "--", "--nocapture", str(self.repo),
                "test", "--manifest-path", "crate/Cargo.toml", str(self.repo),
            ],
        )
        results = json.loads(self.results.read_text(encoding="utf-8"))
        self.assertEqual([result["exit_code"] for result in results], [0, 0, 0])
        self.assertEqual(results[0]["argv"], ["bun", "test", "a.test.ts", "pkg/b.test.ts", "--bail"])

    def test_nonzero_exit_is_recorded_and_fails_the_run(self):
        self.stubs(exit_code=3)
        tests = json.dumps([["bun", "test", "a.test.ts"], ["cargo", "test", "--workspace"]])
        code, _, _ = self.run_main(tests)
        self.assertEqual(code, 1)
        results = json.loads(self.results.read_text(encoding="utf-8"))
        self.assertEqual([result["exit_code"] for result in results], [3, 3])

    def test_a_refused_entry_runs_nothing(self):
        self.stubs()
        tests = json.dumps([["bun", "test", "a.test.ts"], ["bun", "test", "--preload=evil.ts", "a.test.ts"]])
        code, _, stderr = self.run_main(tests)
        self.assertEqual(code, 2)
        self.assertIn("refused", stderr)
        self.assertFalse(self.log.exists())
        self.assertFalse(self.results.exists())

    def test_a_shell_string_runs_nothing(self):
        self.stubs()
        code, _, _ = self.run_main(json.dumps(["bun test a.test.ts; touch pwned"]))
        self.assertEqual(code, 2)
        self.assertFalse(self.log.exists())
        self.assertFalse((self.repo / "pwned").exists())

    def test_a_symlink_that_leaves_the_repository_is_refused(self):
        self.stubs()
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "c.test.ts").write_text("", encoding="utf-8")
        (self.repo / "link").symlink_to(outside)
        code, _, stderr = self.run_main(json.dumps([["bun", "test", "link/c.test.ts"]]))
        self.assertEqual(code, 2)
        self.assertIn("outside the repository", stderr)
        self.assertFalse(self.log.exists())

    def test_a_missing_path_is_refused(self):
        self.stubs()
        code, _, stderr = self.run_main(json.dumps([["bun", "test", "missing.test.ts"]]))
        self.assertEqual(code, 2)
        self.assertIn("does not exist", stderr)

    def test_a_manifest_path_must_be_a_cargo_toml_inside_the_repository(self):
        self.stubs()
        code, _, stderr = self.run_main(json.dumps([["cargo", "test", "--manifest-path", "a.test.ts"]]))
        self.assertEqual(code, 2)
        self.assertIn("Cargo.toml", stderr)
        self.assertFalse(self.log.exists())

    def test_a_program_found_inside_the_repository_is_refused(self):
        self.stubs()
        tools = self.repo / "tools"
        support.make_stub(tools, "bun", f"echo ran >> '{self.log}'")
        code, _, stderr = self.run_main(json.dumps([["bun", "test", "a.test.ts"]]), path=f"{tools}:/usr/bin:/bin")
        self.assertEqual(code, 2)
        self.assertIn("inside the repository", stderr)
        self.assertFalse(self.log.exists())

    def test_a_missing_program_is_refused(self):
        code, _, stderr = self.run_main(json.dumps([["bun", "test", "a.test.ts"]]), path="/nonexistent")
        self.assertEqual(code, 2)
        self.assertIn("not on PATH", stderr)

    def test_a_hung_test_is_killed_and_recorded(self):
        support.make_stub(self.bin, "bun", "sleep 30")
        code, _, _ = self.run_main(json.dumps([["bun", "test", "a.test.ts"]]), timeout=1)
        self.assertEqual(code, 1)
        results = json.loads(self.results.read_text(encoding="utf-8"))
        self.assertEqual(results[0]["exit_code"], 124)

    def test_missing_tests_input_is_refused(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(["--repo", str(self.repo), "--results", str(self.results)], {})
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
