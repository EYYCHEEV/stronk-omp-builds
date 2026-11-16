import os
import subprocess
import sys

from common import COMMIT_PATTERN, TAG_PATTERN, Refusal, require_match


def git_output(source, *arguments):
    completed = subprocess.run(
        ["git", "-C", source, *arguments],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise Refusal(f"git {' '.join(arguments)} failed: {completed.stderr.strip()}")
    return completed.stdout.strip()


def verify(source, tag, commit):
    require_match(TAG_PATTERN, tag, "tag")
    require_match(COMMIT_PATTERN, commit, "commit")
    head = git_output(source, "rev-parse", "HEAD")
    tagged = git_output(source, "rev-parse", "--verify", f"refs/tags/{tag}^{{commit}}")
    if head != commit:
        raise Refusal(f"HEAD is {head}, expected {commit}")
    if tagged != commit:
        raise Refusal(f"tag {tag} points at {tagged}, expected {commit}")


def verify_commit(source, commit):
    require_match(COMMIT_PATTERN, commit, "commit")
    head = git_output(source, "rev-parse", "HEAD")
    if head != commit:
        raise Refusal(f"HEAD is {head}, expected {commit}")


def main(argv=None, environ=None):
    environ = os.environ if environ is None else environ
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 1:
        print("usage: verify_checkout.py SOURCE_DIR", file=sys.stderr)
        return 2
    try:
        if environ.get("MODE") == "precheck":
            verify_commit(arguments[0], environ.get("COMMIT"))
        else:
            verify(arguments[0], environ.get("TAG"), environ.get("COMMIT"))
    except Refusal as refusal:
        print(f"refused: {refusal}", file=sys.stderr)
        return 2
    print("checkout matches the requested commit")
    return 0


if __name__ == "__main__":
    sys.exit(main())
