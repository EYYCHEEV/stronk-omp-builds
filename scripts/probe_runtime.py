import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from common import COMMIT_PATTERN, SHA256_PATTERN, Refusal, authenticated_bun, require_bun_unchanged, require_match
from source_pins import read_pins

VERSION_PATTERN = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
MEASURED_KEYS = ("bun_revision", "bun_sha256", "bun_version", "sqlite_version")
PROBE_SOURCE = (
    'const { Database } = require("bun:sqlite");'
    'const db = new Database(":memory:");'
    'const row = db.query("select sqlite_version() as value").get();'
    "db.close();"
    "process.stdout.write(JSON.stringify("
    "{ sqlite_version: row.value, bun_version: Bun.version, bun_revision: Bun.revision }));"
)
PROBE_TIMEOUT_SECONDS = 60


def locate_bun(source, environ):
    bun, expected = authenticated_bun(environ)
    absolute = bun.resolve()
    if absolute == source or source in absolute.parents:
        raise Refusal(f"authenticated Bun {bun} is inside the source checkout")
    return bun, expected


def check_measured(measured, label):
    if not isinstance(measured, dict) or sorted(measured) != sorted(MEASURED_KEYS):
        raise Refusal(f"{label} must hold exactly {list(MEASURED_KEYS)}")
    require_match(VERSION_PATTERN, measured["sqlite_version"], f"{label} sqlite_version")
    require_match(VERSION_PATTERN, measured["bun_version"], f"{label} bun_version")
    require_match(COMMIT_PATTERN, measured["bun_revision"], f"{label} bun_revision")
    require_match(SHA256_PATTERN, measured["bun_sha256"], f"{label} bun_sha256")
    return dict(measured)


def measure(source, runner_temp, environ):
    source = Path(source).resolve()
    bun, before = locate_bun(source, environ)
    with tempfile.TemporaryDirectory(prefix="runtime-probe-", dir=runner_temp) as folder:
        home = Path(folder) / "home"
        work = Path(folder) / "work"
        home.mkdir(mode=0o700)
        work.mkdir(mode=0o700)
        try:
            completed = subprocess.run(
                [str(bun), "--eval", PROBE_SOURCE],
                cwd=work,
                env={"PATH": "/usr/bin:/bin", "HOME": str(home), "LC_ALL": "C"},
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=PROBE_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            raise Refusal("runtime probe timed out") from None
    if completed.returncode != 0:
        raise Refusal(f"runtime probe exited {completed.returncode}")
    try:
        reported = json.loads(completed.stdout)
    except ValueError:
        raise Refusal("runtime probe did not print one JSON object") from None
    if not isinstance(reported, dict) or sorted(reported) != ["bun_revision", "bun_version", "sqlite_version"]:
        raise Refusal("runtime probe output must hold exactly sqlite_version, bun_version and bun_revision")
    try:
        require_bun_unchanged(bun, before)
    except Refusal:
        raise Refusal("bun changed while the runtime probe ran") from None
    measured = check_measured({**reported, "bun_sha256": before}, "runtime probe")
    expected = read_pins(source)["bun"]
    if measured["bun_version"] != expected:
        raise Refusal(f"measured Bun {measured['bun_version']} is not the source pin {expected}")
    return measured


def load_measured(path):
    try:
        measured = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise Refusal(f"cannot read runtime probe: {error}") from None
    return check_measured(measured, "runtime probe")


def main(argv=None, environ=None):
    environ = os.environ if environ is None else environ
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    runner_temp = environ.get("RUNNER_TEMP")
    try:
        if not runner_temp or not Path(runner_temp).is_dir():
            raise Refusal("RUNNER_TEMP is not a directory")
        measured = measure(args.source, runner_temp, environ)
        with open(args.output, "x", encoding="utf-8") as handle:
            handle.write(json.dumps(measured, indent=2, sort_keys=True) + "\n")
    except (Refusal, OSError) as error:
        reason = str(error) if isinstance(error, Refusal) else f"cannot write runtime probe: {error}"
        print(f"refused: {reason}", file=sys.stderr)
        return 2
    print(f"measured SQLite {measured['sqlite_version']} with Bun {measured['bun_version']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
