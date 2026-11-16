import hashlib
import os
import re
import stat
from pathlib import Path

FORK_REPOSITORY = "EYYCHEEV/oh-my-pi"
BUILDER_REPOSITORY = "EYYCHEEV/stronk-omp-builds"
BINARY_NAME = "omp-darwin-arm64"
MANIFEST_NAME = "build-manifest.json"

TAG_PATTERN = re.compile(r"stronk-omp-[0-9]{8}T[0-9]{6}Z-[0-9]+")
COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
DISPATCH_ID_PATTERN = re.compile(r"[0-9a-f]{32}")


class Refusal(Exception):
    pass


def require_match(pattern, value, label):
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise Refusal(f"{label} does not match {pattern.pattern}")
    return value


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def authenticated_bun(environ):
    raw = environ.get("BUN_PATH")
    if not isinstance(raw, str) or not os.path.isabs(raw):
        raise Refusal("BUN_PATH must be the absolute path the Bun authentication step reported")
    expected = require_match(SHA256_PATTERN, environ.get("BUN_SHA256"), "BUN_SHA256")
    path = Path(raw)
    try:
        info = os.lstat(path)
    except OSError:
        raise Refusal(f"authenticated Bun {path} is missing") from None
    if not stat.S_ISREG(info.st_mode):
        raise Refusal(f"authenticated Bun {path} is not a regular file")
    require_bun_unchanged(path, expected)
    return path, expected


def require_bun_unchanged(path, expected):
    try:
        actual = sha256_file(path)
    except OSError:
        raise Refusal(f"authenticated Bun {path} cannot be read") from None
    if actual != expected:
        raise Refusal(f"Bun at {path} has sha256 {actual}, not the authenticated {expected}")
