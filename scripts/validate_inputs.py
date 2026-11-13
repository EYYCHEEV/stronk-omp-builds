import os
import sys

from common import BUILDER_REPOSITORY, COMMIT_PATTERN, TAG_PATTERN, Refusal, require_match
from run_tests import parse_tests

MAIN_REF = "refs/heads/main"


def validate(environ):
    if environ.get("EVENT") != "workflow_dispatch":
        raise Refusal("only workflow_dispatch runs are accepted")
    if environ.get("REF") != MAIN_REF:
        raise Refusal(f"only runs from {MAIN_REF} are accepted")
    if environ.get("REPOSITORY") != BUILDER_REPOSITORY:
        raise Refusal(f"only runs in {BUILDER_REPOSITORY} are accepted")
    require_match(TAG_PATTERN, environ.get("TAG"), "tag")
    require_match(COMMIT_PATTERN, environ.get("COMMIT"), "commit")
    parse_tests(environ.get("TESTS"))


def main(environ=None):
    environ = os.environ if environ is None else environ
    try:
        validate(environ)
    except Refusal as refusal:
        print(f"refused: {refusal}", file=sys.stderr)
        return 2
    print("inputs accepted")
    return 0


if __name__ == "__main__":
    sys.exit(main())
