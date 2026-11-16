import hashlib
import os
import sys

from common import (
    BUILDER_REPOSITORY, COMMIT_PATTERN, DISPATCH_ID_PATTERN, SHA256_PATTERN, TAG_PATTERN, Refusal, require_match,
)
from run_tests import parse_tests

MAIN_REF = "refs/heads/main"
MODES = ("release", "precheck")


def validate(environ):
    if environ.get("EVENT") != "workflow_dispatch":
        raise Refusal("only workflow_dispatch runs are accepted")
    if environ.get("REF") != MAIN_REF:
        raise Refusal(f"only runs from {MAIN_REF} are accepted")
    if environ.get("REPOSITORY") != BUILDER_REPOSITORY:
        raise Refusal(f"only runs in {BUILDER_REPOSITORY} are accepted")
    mode = environ.get("MODE")
    if mode not in MODES:
        raise Refusal(f"mode {mode!r} is not one of {list(MODES)}")
    if mode == "release":
        require_match(TAG_PATTERN, environ.get("TAG"), "tag")
        if environ.get("TESTS_SHA256"):
            raise Refusal("tests_sha256 is for precheck mode only")
        if environ.get("DISPATCH_ID"):
            raise Refusal("dispatch_id is for precheck mode only")
    else:
        if environ.get("TAG"):
            raise Refusal("precheck mode takes no tag")
        require_match(DISPATCH_ID_PATTERN, environ.get("DISPATCH_ID"), "dispatch_id")
        expected = require_match(SHA256_PATTERN, environ.get("TESTS_SHA256"), "tests_sha256")
        actual = hashlib.sha256((environ.get("TESTS") or "").encode("utf-8")).hexdigest()
        if actual != expected:
            raise Refusal(f"tests input sha256 is {actual}, but tests_sha256 says {expected}")
    require_match(COMMIT_PATTERN, environ.get("COMMIT"), "commit")
    parse_tests(environ.get("TESTS"))
    return mode


def main(environ=None):
    environ = os.environ if environ is None else environ
    try:
        mode = validate(environ)
    except Refusal as refusal:
        print(f"refused: {refusal}", file=sys.stderr)
        return 2
    print(f"inputs accepted for {mode}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
