import json
import os
import re
import sys
import tomllib
from pathlib import Path

from common import Refusal

PACKAGE_MANAGER = re.compile(r"bun@(?:>=)?([0-9]+\.[0-9]+\.[0-9]+)")
OMP_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
RUST_CHANNEL = re.compile(r"nightly-[0-9]{4}-[0-9]{2}-[0-9]{2}|[0-9]+\.[0-9]+\.[0-9]+")


def load_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise Refusal(f"cannot read {path}: {error}") from None


def read_pins(source):
    root = Path(source)
    root_manifest = load_json(root / "package.json")
    manager = root_manifest.get("packageManager") if isinstance(root_manifest, dict) else None
    manager_match = PACKAGE_MANAGER.fullmatch(manager) if isinstance(manager, str) else None
    if manager_match is None:
        raise Refusal(f"package.json packageManager {manager!r} is not bun@X.Y.Z or bun@>=X.Y.Z")
    try:
        toolchain = tomllib.loads((root / "rust-toolchain.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise Refusal(f"cannot read rust-toolchain.toml: {error}") from None
    channel = toolchain.get("toolchain", {}).get("channel")
    if not isinstance(channel, str) or RUST_CHANNEL.fullmatch(channel) is None:
        raise Refusal(f"rust-toolchain.toml channel {channel!r} is not nightly-YYYY-MM-DD or X.Y.Z")
    agent_manifest = load_json(root / "packages" / "coding-agent" / "package.json")
    version = agent_manifest.get("version") if isinstance(agent_manifest, dict) else None
    if not isinstance(version, str) or OMP_VERSION.fullmatch(version) is None:
        raise Refusal(f"coding-agent version {version!r} is not X.Y.Z")
    return {"bun": manager_match.group(1), "rust": channel, "omp": version}


def main(argv=None, environ=None):
    environ = os.environ if environ is None else environ
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 2 or arguments[0] != "--source":
        print("usage: source_pins.py --source SOURCE_DIR", file=sys.stderr)
        return 2
    try:
        pins = read_pins(arguments[1])
    except Refusal as refusal:
        print(f"refused: {refusal}", file=sys.stderr)
        return 2
    lines = "".join(f"{key}={value}\n" for key, value in pins.items())
    sys.stdout.write(lines)
    output_path = environ.get("GITHUB_OUTPUT")
    if output_path:
        with open(output_path, "a", encoding="utf-8") as handle:
            handle.write(lines)
    return 0


if __name__ == "__main__":
    sys.exit(main())
