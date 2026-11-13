import json
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "checks"))

TAG = "stronk-omp-20261101T151318Z-9111"
COMMIT = "42c839a139a65eea12051db999d6d4bb360ce2fa"
OTHER_COMMIT = "0123456789abcdef0123456789abcdef01234567"
WORKFLOW = ROOT / ".github" / "workflows" / "mac-build.yml"


def make_stub(directory, name, body):
    path = Path(directory) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def make_source(root, package_manager="bun@>=1.4.4", channel="nightly-2026-10-06", version="18.14.2"):
    root = Path(root)
    (root / "packages" / "coding-agent").mkdir(parents=True, exist_ok=True)
    (root / "package.json").write_text(json.dumps({"packageManager": package_manager}), encoding="utf-8")
    (root / "rust-toolchain.toml").write_text(f'[toolchain]\nchannel = "{channel}"\n', encoding="utf-8")
    (root / "packages" / "coding-agent" / "package.json").write_text(json.dumps({"version": version}), encoding="utf-8")
    return root


def runner_environment(**overrides):
    environment = {
        "TAG": TAG,
        "COMMIT": COMMIT,
        "ImageOS": "macos15",
        "ImageVersion": "20261104.0514.1",
        "RUNNER_OS": "macOS",
        "RUNNER_ARCH": "ARM64",
        "GITHUB_REPOSITORY": "EYYCHEEV/stronk-omp-builds",
        "GITHUB_SHA": OTHER_COMMIT,
        "GITHUB_RUN_ID": "1234567890",
        "GITHUB_RUN_ATTEMPT": "1",
    }
    environment.update(overrides)
    return environment


def git(directory, *arguments):
    completed = subprocess.run(
        ["git", "-c", "user.name=test", "-c", "user.email=test@example.invalid", "-C", str(directory), *arguments],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.strip()


class TemporaryDirectory(tempfile.TemporaryDirectory):
    def __enter__(self):
        return Path(os.path.realpath(super().__enter__()))
