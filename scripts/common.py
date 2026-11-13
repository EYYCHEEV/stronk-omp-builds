import hashlib
import re

FORK_REPOSITORY = "EYYCHEEV/oh-my-pi"
BUILDER_REPOSITORY = "EYYCHEEV/stronk-omp-builds"
BINARY_NAME = "omp-darwin-arm64"
MANIFEST_NAME = "build-manifest.json"

TAG_PATTERN = re.compile(r"stronk-omp-[0-9]{8}T[0-9]{6}Z-[0-9]+")
COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


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
