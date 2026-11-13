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


TESTED_FILE = "packages/tui/test/process-terminal-input-recovery.test.ts"
PATTERN = "reopens a Bun-destroyed TTY read stream and delivers the next input"


def bun_entry(*arguments):
    return ["bun", "test", *arguments]


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
        refused(self, json.dumps([["bun", "test"] + ["a.test.ts"] * 70]))
        refused(self, json.dumps([["bun", "test", "a" * 300]]))

    def test_accepts_128_entries_and_refuses_129(self):
        entry = ["bun", "test", "a.test.ts"]
        self.assertEqual(len(parse_tests(json.dumps([entry] * 128))), 128)
        refused(self, json.dumps([entry] * 129))

    def test_accepts_49152_characters_and_refuses_49153(self):
        text = json.dumps([["bun", "test", "a.test.ts"]])
        self.assertEqual(len(parse_tests(text + " " * (49152 - len(text)))), 1)
        refused(self, text + " " * (49153 - len(text)))

    def test_accepts_the_test_name_pattern_after_one_value_argument(self):
        entries = parse_tests(json.dumps([bun_entry(TESTED_FILE, "--test-name-pattern", PATTERN)]))
        self.assertEqual(entries[0].argv, bun_entry(TESTED_FILE, "--test-name-pattern", PATTERN))
        self.assertEqual(entries[0].paths, [TESTED_FILE])

    def test_accepts_the_test_name_pattern_with_an_equals_sign(self):
        entries = parse_tests(json.dumps([bun_entry(TESTED_FILE, f"--test-name-pattern={PATTERN}")]))
        self.assertEqual(entries[0].paths, [TESTED_FILE])

    def test_accepts_the_option_before_the_paths_and_more_than_once(self):
        entries = parse_tests(
            json.dumps(
                [
                    bun_entry("--test-name-pattern", "web.run", TESTED_FILE),
                    bun_entry(TESTED_FILE, "--test-name-pattern", "one", "--test-name-pattern=two", "--bail"),
                ]
            )
        )
        self.assertEqual([entry.paths for entry in entries], [[TESTED_FILE], [TESTED_FILE]])

    def test_accepts_spaces_punctuation_and_path_like_text_in_a_pattern(self):
        values = (
            "inherits the wrapper call.s signal and onUpdate for a bare invokeTool",
            "web.run",
            "a|b (c) [d] {e} $f ; g 'h' \"i\" * ? \\ `j` > < & ~ !",
            "../../not a path",
            "/etc/passwd",
            "x" * 256,
        )
        for value in values:
            with self.subTest(value=value):
                detached = parse_tests(json.dumps([bun_entry(TESTED_FILE, "--test-name-pattern", value)]))
                attached = parse_tests(json.dumps([bun_entry(TESTED_FILE, f"--test-name-pattern={value}")]))
                self.assertEqual(detached[0].paths, [TESTED_FILE])
                self.assertEqual(attached[0].paths, [TESTED_FILE])

    def test_accepts_a_leading_hyphen_only_when_the_value_is_attached(self):
        entries = parse_tests(json.dumps([bun_entry(TESTED_FILE, "--test-name-pattern=--preload=evil.ts")]))
        self.assertEqual(entries[0].paths, [TESTED_FILE])

    def test_refuses_an_empty_pattern(self):
        refused(self, json.dumps([bun_entry(TESTED_FILE, "--test-name-pattern", "")]))
        refused(self, json.dumps([bun_entry(TESTED_FILE, "--test-name-pattern=")]))

    def test_refuses_a_pattern_longer_than_256_characters(self):
        refused(self, json.dumps([bun_entry(TESTED_FILE, "--test-name-pattern", "x" * 257)]))
        refused(self, json.dumps([bun_entry(TESTED_FILE, "--test-name-pattern=" + "x" * 257)]))

    def test_refuses_non_ascii_and_control_characters_in_a_pattern(self):
        for value in ("caf\u00e9", "line\nbreak", "tab\there", "nul\x00byte", "esc\x1b[0m", "del\x7f"):
            with self.subTest(value=value):
                refused(self, json.dumps([bun_entry(TESTED_FILE, "--test-name-pattern", value)]))
                refused(self, json.dumps([bun_entry(TESTED_FILE, f"--test-name-pattern={value}")]))

    def test_refuses_the_option_without_a_value(self):
        refused(self, json.dumps([bun_entry(TESTED_FILE, "--test-name-pattern")]))
        refused(self, json.dumps([bun_entry("--test-name-pattern")]))

    def test_refuses_a_separate_value_that_starts_with_a_hyphen(self):
        for value in ("--preload=evil.ts", "-x", "--bail", "-"):
            with self.subTest(value=value):
                refused(self, json.dumps([bun_entry(TESTED_FILE, "--test-name-pattern", value)]))

    def test_a_pattern_does_not_count_as_a_path_to_test(self):
        refused(self, json.dumps([bun_entry("--test-name-pattern", PATTERN)]))
        refused(self, json.dumps([bun_entry(f"--test-name-pattern={PATTERN}")]))

    def test_other_options_stay_refused_next_to_a_pattern(self):
        for entry in (
            bun_entry(TESTED_FILE, "--test-name-pattern", PATTERN, "--preload=evil.ts"),
            bun_entry(TESTED_FILE, f"--test-name-pattern={PATTERN}", "--config=evil.toml"),
            bun_entry(TESTED_FILE, "-t", "web.run"),
            bun_entry(TESTED_FILE, "-t=" + PATTERN),
            bun_entry(TESTED_FILE, "--test-name-patterns=x"),
            bun_entry(TESTED_FILE, "--test-name-pattern-x"),
            bun_entry(TESTED_FILE, "--test-name-pattern:x"),
            bun_entry(TESTED_FILE, "--Test-Name-Pattern=x"),
        ):
            with self.subTest(entry=entry):
                refused(self, json.dumps([entry]))

    def test_cargo_refuses_the_test_name_pattern(self):
        for entry in (
            ["cargo", "test", "--test-name-pattern", "x"],
            ["cargo", "test", "--test-name-pattern=x"],
            ["cargo", "test", "--", "--test-name-pattern", "x"],
            ["cargo", "test", "-p", "demo", "--test-name-pattern", PATTERN],
        ):
            with self.subTest(entry=entry):
                refused(self, json.dumps([entry]))


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

    def test_a_pattern_reaches_bun_as_one_argument_without_a_shell(self):
        self.stubs()
        value = "has spaces; and 'quotes' $HOME | more > out"
        tests = json.dumps(
            [
                bun_entry("a.test.ts", "--test-name-pattern", value),
                bun_entry("pkg/b.test.ts", f"--test-name-pattern={value}"),
            ]
        )
        code, _, _ = self.run_main(tests)
        self.assertEqual(code, 0)
        lines = self.log.read_text(encoding="utf-8").splitlines()
        self.assertEqual(
            lines,
            [
                "test", "a.test.ts", "--test-name-pattern", value, str(self.repo),
                "test", "pkg/b.test.ts", f"--test-name-pattern={value}", str(self.repo),
            ],
        )
        self.assertFalse((self.repo / "out").exists())

    def test_the_longest_accepted_list_runs_every_entry(self):
        self.stubs()
        tests = json.dumps([bun_entry("a.test.ts", "--test-name-pattern", f"case {number}") for number in range(128)])
        code, _, _ = self.run_main(tests)
        self.assertEqual(code, 0)
        results = json.loads(self.results.read_text(encoding="utf-8"))
        self.assertEqual(len(results), 128)
        self.assertEqual({result["exit_code"] for result in results}, {0})

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
