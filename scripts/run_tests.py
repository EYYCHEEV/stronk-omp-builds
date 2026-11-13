import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

from common import Refusal

MAX_JSON_CHARS = 49152
MAX_ENTRIES = 128
MAX_ARGUMENTS = 64
MAX_ARGUMENT_CHARS = 256
DEFAULT_TIMEOUT_SECONDS = 3000
TIMEOUT_EXIT_CODE = 124

PATH_ARGUMENT = re.compile(r"[A-Za-z0-9_.][A-Za-z0-9_./+@-]*")
CRATE_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_-]{0,63}")
TEST_FILTER = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_:]{0,127}")
BUN_TIMEOUT = re.compile(r"--timeout=[0-9]{1,7}")
HARNESS_THREADS = re.compile(r"--test-threads=[0-9]{1,3}")

BUN_FLAGS = frozenset({"--bail", "--only-failures"})
PATTERN_OPTION = "--test-name-pattern"
PATTERN_PREFIX = PATTERN_OPTION + "="
CARGO_FLAGS = frozenset(
    {
        "--workspace",
        "--lib",
        "--bins",
        "--tests",
        "--doc",
        "--release",
        "--locked",
        "--offline",
        "--no-fail-fast",
        "--quiet",
        "-q",
    }
)
CARGO_VALUE_FLAGS = frozenset({"-p", "--package", "--test", "--exclude"})
CARGO_LONG_VALUE_FLAGS = frozenset({"--package", "--test", "--exclude"})
HARNESS_FLAGS = frozenset({"--nocapture", "--exact", "--ignored", "--include-ignored", "--show-output", "--quiet", "-q"})


class Entry(NamedTuple):
    argv: list
    paths: list
    manifests: list


def check_path(label, argument):
    if PATH_ARGUMENT.fullmatch(argument) is None:
        raise Refusal(f"{label}: {argument!r} is not a plain repository path")
    if any(part in ("", "..") for part in argument.split("/")):
        raise Refusal(f"{label}: {argument!r} leaves the repository")
    return argument


def check_bun(label, rest):
    paths = []
    arguments = iter(rest)
    for argument in arguments:
        if argument == PATTERN_OPTION:
            value = next(arguments, None)
            if value is None or value.startswith("-"):
                raise Refusal(f"{label}: {PATTERN_OPTION} needs one value that does not start with a hyphen")
            continue
        if argument.startswith(PATTERN_PREFIX):
            if argument == PATTERN_PREFIX:
                raise Refusal(f"{label}: {PATTERN_PREFIX} needs a value")
            continue
        if argument.startswith("-"):
            if argument in BUN_FLAGS or BUN_TIMEOUT.fullmatch(argument):
                continue
            raise Refusal(f"{label}: option {argument!r} is not allowed")
        paths.append(check_path(label, argument))
    if not paths:
        raise Refusal(f"{label}: bun test needs at least one repository path")
    return paths, []


def check_harness_argument(label, argument):
    if argument in HARNESS_FLAGS or HARNESS_THREADS.fullmatch(argument) or TEST_FILTER.fullmatch(argument):
        return
    raise Refusal(f"{label}: test harness argument {argument!r} is not allowed")


def check_cargo(label, rest):
    manifests = []
    index = 0
    after_separator = False
    while index < len(rest):
        argument = rest[index]
        if after_separator:
            check_harness_argument(label, argument)
        elif argument == "--":
            after_separator = True
        elif argument in CARGO_FLAGS:
            pass
        elif argument in CARGO_VALUE_FLAGS:
            index += 1
            if index >= len(rest) or CRATE_NAME.fullmatch(rest[index]) is None:
                raise Refusal(f"{label}: {argument} needs a plain crate or test name")
        elif argument == "--manifest-path":
            index += 1
            if index >= len(rest):
                raise Refusal(f"{label}: --manifest-path needs a path")
            manifests.append(check_path(label, rest[index]))
        elif argument.startswith("--") and "=" in argument:
            name, value = argument.split("=", 1)
            if name in CARGO_LONG_VALUE_FLAGS and CRATE_NAME.fullmatch(value):
                pass
            elif name == "--manifest-path":
                manifests.append(check_path(label, value))
            else:
                raise Refusal(f"{label}: option {argument!r} is not allowed")
        elif argument.startswith("-"):
            raise Refusal(f"{label}: option {argument!r} is not allowed")
        elif TEST_FILTER.fullmatch(argument) is None:
            raise Refusal(f"{label}: {argument!r} is not a plain test name filter")
        index += 1
    return [], manifests


def argument_limit(argument):
    if argument.startswith(PATTERN_PREFIX):
        return MAX_ARGUMENT_CHARS + len(PATTERN_PREFIX)
    return MAX_ARGUMENT_CHARS


def check_entry(index, entry):
    label = f"tests[{index}]"
    if not isinstance(entry, list):
        raise Refusal(f"{label} is not an argv list; shell strings are refused")
    if not 2 <= len(entry) <= MAX_ARGUMENTS:
        raise Refusal(f"{label} must have between 2 and {MAX_ARGUMENTS} arguments")
    for argument in entry:
        if not isinstance(argument, str) or not 0 < len(argument) <= argument_limit(argument):
            raise Refusal(f"{label} holds an argument that is not a short non-empty string")
        if not argument.isascii() or not argument.isprintable():
            raise Refusal(f"{label} holds an argument with control or non-ASCII characters")
    program, verb, *rest = entry
    if program not in ("bun", "cargo") or verb != "test":
        raise Refusal(f"{label} must start with bun test or cargo test")
    if program == "bun":
        paths, manifests = check_bun(label, rest)
    else:
        paths, manifests = check_cargo(label, rest)
    return Entry(list(entry), paths, manifests)


def parse_tests(raw):
    if not isinstance(raw, str) or not raw.strip():
        raise Refusal("tests input is missing")
    if len(raw) > MAX_JSON_CHARS:
        raise Refusal(f"tests input is longer than {MAX_JSON_CHARS} characters")
    try:
        entries = json.loads(raw)
    except (ValueError, RecursionError) as error:
        raise Refusal(f"tests input is not valid JSON: {error}") from None
    if not isinstance(entries, list) or not entries:
        raise Refusal("tests input must be a non-empty JSON list of argv lists")
    if len(entries) > MAX_ENTRIES:
        raise Refusal(f"tests input has more than {MAX_ENTRIES} entries")
    return [check_entry(index, entry) for index, entry in enumerate(entries)]


def resolve_inside(root, relative, label, want_file_named=None):
    target = (root / relative).resolve()
    if target != root and root not in target.parents:
        raise Refusal(f"{label}: {relative!r} resolves outside the repository")
    if not target.exists():
        raise Refusal(f"{label}: {relative!r} does not exist")
    if want_file_named is not None and (not target.is_file() or target.name != want_file_named):
        raise Refusal(f"{label}: {relative!r} is not a {want_file_named} file")
    return target


def locate(program, root, label):
    found = shutil.which(program)
    if found is None:
        raise Refusal(f"{label}: {program} is not on PATH")
    absolute = Path(os.path.abspath(found))
    if absolute == root or root in absolute.parents:
        raise Refusal(f"{label}: {program} resolves to {absolute}, inside the repository")
    return str(absolute)


def plan(entries, root):
    executables = []
    for index, entry in enumerate(entries):
        label = f"tests[{index}]"
        for relative in entry.paths:
            resolve_inside(root, relative, label)
        for relative in entry.manifests:
            resolve_inside(root, relative, label, want_file_named="Cargo.toml")
        executables.append(locate(entry.argv[0], root, label))
    return executables


def run_entry(executable, argv, root, timeout):
    process = subprocess.Popen(
        [executable, *argv[1:]],
        cwd=root,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        return process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        return TIMEOUT_EXIT_CODE


def main(argv=None, environ=None):
    environ = os.environ if environ is None else environ
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--results", required=True)
    parser.add_argument("--timeout-seconds", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    args = parser.parse_args(argv)
    root = Path(args.repo).resolve()
    try:
        if not root.is_dir():
            raise Refusal(f"{args.repo} is not a directory")
        entries = parse_tests(environ.get("TESTS"))
        executables = plan(entries, root)
    except Refusal as refusal:
        print(f"refused: {refusal}", file=sys.stderr)
        return 2
    results = []
    for entry, executable in zip(entries, executables):
        print(f"==> {' '.join(entry.argv)}", flush=True)
        code = run_entry(executable, entry.argv, root, args.timeout_seconds)
        print(f"==> exit {code}", flush=True)
        results.append({"argv": entry.argv, "exit_code": code})
    Path(args.results).write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    return 0 if all(result["exit_code"] == 0 for result in results) else 1


if __name__ == "__main__":
    sys.exit(main())
