import os
import shutil
import subprocess
import sys
import tempfile

from common import Refusal

SMOKE_TIMEOUT_SECONDS = 120


def run_binary(binary, arguments, env, workspace):
    try:
        return subprocess.run(
            [binary, *arguments],
            cwd=workspace,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=SMOKE_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise Refusal(f"{' '.join(arguments)} did not finish within {SMOKE_TIMEOUT_SECONDS} seconds") from None


def smoke(binary, version):
    binary = os.path.abspath(binary)
    workspace = tempfile.mkdtemp(prefix="omp-smoke-")
    try:
        home = os.path.join(workspace, "home")
        data = os.path.join(workspace, "xdg")
        os.makedirs(home)
        os.makedirs(data)
        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": home,
            "XDG_DATA_HOME": data,
            "TMPDIR": workspace,
        }
        shown = run_binary(binary, ["--version"], env, workspace)
        if shown.returncode != 0:
            raise Refusal(f"--version exited {shown.returncode}")
        if shown.stdout.strip() != f"omp/{version}":
            raise Refusal(f"--version printed {shown.stdout.strip()!r}, expected 'omp/{version}'")
        tested = run_binary(binary, ["--smoke-test"], env, workspace)
        if tested.returncode != 0:
            raise Refusal(f"--smoke-test exited {tested.returncode}")
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def main(argv=None):
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 4 or arguments[0] != "--binary" or arguments[2] != "--version":
        print("usage: smoke.py --binary PATH --version X.Y.Z", file=sys.stderr)
        return 2
    try:
        smoke(arguments[1], arguments[3])
    except Refusal as refusal:
        print(f"refused: {refusal}", file=sys.stderr)
        return 2
    print("smoke passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
