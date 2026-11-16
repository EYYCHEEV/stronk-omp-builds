import argparse
import json
import os
import platform
import sys
from pathlib import Path

from common import (
    BINARY_NAME,
    BUILDER_REPOSITORY,
    COMMIT_PATTERN,
    DISPATCH_ID_PATTERN,
    FORK_REPOSITORY,
    MANIFEST_NAME,
    SHA256_PATTERN,
    TAG_PATTERN,
    Refusal,
    require_match,
    sha256_file,
)
from probe_runtime import check_measured, load_measured
from source_pins import read_pins

SCHEMA = 1


def load_results(path):
    try:
        results = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise Refusal(f"cannot read test results: {error}") from None
    if not isinstance(results, list) or not results:
        raise Refusal("test results must be a non-empty list")
    checked = []
    for result in results:
        argv = result.get("argv") if isinstance(result, dict) else None
        code = result.get("exit_code") if isinstance(result, dict) else None
        if not isinstance(argv, list) or not all(isinstance(item, str) for item in argv) or type(code) is not int:
            raise Refusal("each test result needs an argv list and an integer exit_code")
        if code != 0:
            raise Refusal(f"test {' '.join(argv)} exited {code}")
        checked.append({"argv": argv, "exit_code": code})
    return checked


def require_env(environ, name):
    value = environ.get(name)
    if not value:
        raise Refusal(f"environment variable {name} is not set")
    return value


def runner_record(environ):
    return {
        "image_os": require_env(environ, "ImageOS"),
        "image_version": require_env(environ, "ImageVersion"),
        "os": require_env(environ, "RUNNER_OS"),
        "arch": require_env(environ, "RUNNER_ARCH"),
        "macos_version": platform.mac_ver()[0],
    }


def builder_record(environ):
    return {
        "repository": require_env(environ, "GITHUB_REPOSITORY"),
        "commit": require_env(environ, "GITHUB_SHA"),
        "run_id": require_env(environ, "GITHUB_RUN_ID"),
        "run_attempt": require_env(environ, "GITHUB_RUN_ATTEMPT"),
    }


def build_manifest(source, binary, results_path, probe_path, environ):
    tag = require_match(TAG_PATTERN, environ.get("TAG"), "tag")
    commit = require_match(COMMIT_PATTERN, environ.get("COMMIT"), "commit")
    pins = read_pins(source)
    binary = Path(binary)
    if binary.name != BINARY_NAME or not binary.is_file():
        raise Refusal(f"{binary} is not the {BINARY_NAME} file")
    return {
        "schema": SCHEMA,
        "fork": {"repository": FORK_REPOSITORY, "tag": tag, "commit": commit},
        "binary": {
            "name": BINARY_NAME,
            "sha256": sha256_file(binary),
            "size": binary.stat().st_size,
        },
        "omp_version": pins["omp"],
        "toolchain": {"bun": pins["bun"], "rust_channel": pins["rust"]},
        "measured": load_measured(probe_path),
        "runner": runner_record(environ),
        "builder": builder_record(environ),
        "tests": load_results(results_path),
    }


def write_manifest(source, binary, results_path, probe_path, output, environ):
    manifest = build_manifest(source, binary, results_path, probe_path, environ)
    Path(output).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def write_precheck_record(results_path, probe_path, output, environ):
    if environ.get("TAG"):
        raise Refusal("a pre-check record takes no tag")
    record = {
        "schema": SCHEMA,
        "mode": "precheck",
        "dispatch_id": require_match(DISPATCH_ID_PATTERN, environ.get("DISPATCH_ID"), "dispatch_id"),
        "fork": {
            "repository": FORK_REPOSITORY,
            "commit": require_match(COMMIT_PATTERN, environ.get("COMMIT"), "commit"),
        },
        "measured": load_measured(probe_path),
        "runner": runner_record(environ),
        "builder": builder_record(environ),
        "tests": load_results(results_path),
    }
    with open(output, "x", encoding="utf-8") as handle:
        handle.write(json.dumps(record, indent=2, sort_keys=True) + "\n")
    return record


def as_object(value, label):
    if not isinstance(value, dict):
        raise Refusal(f"manifest {label} is not an object")
    return value


def verify_artifact(directory, environ):
    tag = require_match(TAG_PATTERN, environ.get("TAG"), "tag")
    commit = require_match(COMMIT_PATTERN, environ.get("COMMIT"), "commit")
    root = Path(directory)
    names = sorted(entry.name for entry in root.iterdir())
    if names != sorted([BINARY_NAME, MANIFEST_NAME]):
        raise Refusal(f"artifact holds {names}, expected exactly {[BINARY_NAME, MANIFEST_NAME]}")
    try:
        manifest = json.loads((root / MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise Refusal(f"cannot read {MANIFEST_NAME}: {error}") from None
    if not isinstance(manifest, dict) or manifest.get("schema") != SCHEMA:
        raise Refusal(f"{MANIFEST_NAME} schema is not {SCHEMA}")
    fork = as_object(manifest.get("fork"), "fork")
    if fork != {"repository": FORK_REPOSITORY, "tag": tag, "commit": commit}:
        raise Refusal(f"manifest fork record {fork} does not match {FORK_REPOSITORY} {tag} {commit}")
    builder = as_object(manifest.get("builder"), "builder")
    expected_builder = {
        "repository": BUILDER_REPOSITORY,
        "commit": environ.get("GITHUB_SHA"),
        "run_id": environ.get("GITHUB_RUN_ID"),
    }
    for key, expected in expected_builder.items():
        if not expected or builder.get(key) != expected:
            raise Refusal(f"manifest builder {key} {builder.get(key)!r} does not match this run")
    recorded = as_object(manifest.get("binary"), "binary")
    if recorded.get("name") != BINARY_NAME:
        raise Refusal("manifest binary name is not " + BINARY_NAME)
    require_match(SHA256_PATTERN, recorded.get("sha256"), "manifest binary sha256")
    actual = sha256_file(root / BINARY_NAME)
    if actual != recorded["sha256"]:
        raise Refusal(f"binary sha256 {actual} differs from manifest {recorded['sha256']}")
    tests = manifest.get("tests")
    if not isinstance(tests, list) or not tests or any(
        not isinstance(test, dict) or test.get("exit_code") != 0 for test in tests
    ):
        raise Refusal("manifest does not record passing tests")
    check_measured(manifest.get("measured"), "manifest measured")
    return manifest


def main(argv=None, environ=None):
    environ = os.environ if environ is None else environ
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    write = commands.add_parser("write")
    write.add_argument("--source", required=True)
    write.add_argument("--binary", required=True)
    write.add_argument("--tests", required=True)
    write.add_argument("--probe", required=True)
    write.add_argument("--output", required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--artifact-dir", required=True)
    precheck = commands.add_parser("precheck")
    precheck.add_argument("--tests", required=True)
    precheck.add_argument("--probe", required=True)
    precheck.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "write":
            manifest = write_manifest(args.source, args.binary, args.tests, args.probe, args.output, environ)
            print(f"manifest written: {manifest['binary']['sha256']}")
        elif args.command == "precheck":
            record = write_precheck_record(args.tests, args.probe, args.output, environ)
            print(f"pre-check record written: SQLite {record['measured']['sqlite_version']}")
        else:
            manifest = verify_artifact(args.artifact_dir, environ)
            print(f"artifact verified: {manifest['binary']['sha256']}")
    except (Refusal, FileExistsError) as refusal:
        print(f"refused: {refusal}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
