import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from common import Refusal, sha256_file

SIGNER_FINGERPRINT = "F3DCC08A8572C0749B3E18888EAB4D40A7B22B59"
KEY_FILE = Path(__file__).with_name("bun-release-key.asc")
ARCHIVE_NAME = "bun-darwin-aarch64.zip"
BINARY_MEMBER = "bun-darwin-aarch64/bun"
VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")


def checked_run(arguments, environment, phase, timeout=30):
    try:
        result = subprocess.run(
            arguments, capture_output=True, text=True, env=environment,
            timeout=timeout, stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise Refusal(f"Bun {phase} could not complete") from None
    if result.returncode:
        raise Refusal(f"Bun {phase} failed with exit code {result.returncode}")
    return result


def verify_archive(folder, gpg, environment):
    command = [
        gpg, "--no-options", "--batch", "--no-tty", "--no-autostart",
        "--homedir", environment["GNUPGHOME"], "--no-auto-key-retrieve",
    ]
    keys = checked_run(
        [*command, "--with-colons", "--show-keys", str(KEY_FILE)],
        environment, "public-key inspection",
    )
    fingerprints = []
    primary = False
    for line in keys.stdout.splitlines():
        fields = line.split(":")
        if fields[0] in {"sec", "ssb"}:
            raise Refusal("Bun verification accepts only public key material")
        if fields[0] == "pub":
            primary = True
        elif fields[0] == "sub":
            primary = False
        elif fields[0] == "fpr" and primary:
            fingerprints.append(fields[9] if len(fields) > 9 else "")
            primary = False
    if fingerprints != [SIGNER_FINGERPRINT]:
        raise Refusal("Bun public key does not match the pinned primary fingerprint")
    checked_run([*command, "--import", str(KEY_FILE)], environment, "public-key import")
    verified = checked_run(
        [*command, "--status-fd", "2", "--decrypt", str(folder / "SHASUMS256.txt.asc")],
        environment, "manifest signature verification",
    )
    statuses = [
        line.split() for line in verified.stderr.splitlines()
        if line.startswith("[GNUPG:] ")
    ]
    valid = [fields for fields in statuses if len(fields) >= 3 and fields[1] == "VALIDSIG"]
    good = [fields for fields in statuses if len(fields) >= 3 and fields[1] == "GOODSIG"]
    failed = {"BADSIG", "ERRSIG", "EXPSIG", "EXPKEYSIG", "REVKEYSIG", "NODATA", "FAILURE"}
    if (
        len(valid) != 1 or valid[0][2] != SIGNER_FINGERPRINT or len(good) != 1
        or any(len(fields) >= 2 and fields[1] in failed for fields in statuses)
    ):
        raise Refusal("Bun manifest lacks a good signature from the pinned signer")
    checksums = []
    for line in verified.stdout.splitlines():
        match = re.fullmatch(r"([0-9a-fA-F]{64}) [ *]" + re.escape(ARCHIVE_NAME), line)
        if match:
            checksums.append(match.group(1).lower())
    if len(checksums) != 1 or sha256_file(folder / ARCHIVE_NAME) != checksums[0]:
        raise Refusal("Bun archive does not match exactly one authenticated checksum")


def setup_bun(version, runner_temp, path_output):
    if not isinstance(version, str) or VERSION.fullmatch(version) is None:
        raise Refusal("Bun version must be exactly X.Y.Z")
    root = Path(runner_temp)
    output = Path(path_output)
    if not root.is_absolute() or not root.is_dir() or not output.is_absolute():
        raise Refusal("Bun bootstrap needs absolute runner-temp and PATH-output paths")
    gpg = shutil.which("gpg")
    if not gpg or not Path(gpg).is_absolute():
        raise Refusal("GnuPG is not available on the runner image")
    folder = Path(tempfile.mkdtemp(prefix="verified-bun-", dir=root))
    home = folder / "home"
    keyring = folder / "gnupg"
    home.mkdir(mode=0o700)
    keyring.mkdir(mode=0o700)
    environment = {
        "PATH": "/usr/bin:/bin", "LC_ALL": "C", "HOME": str(home),
        "GNUPGHOME": str(keyring),
    }
    release = f"https://github.com/oven-sh/bun/releases/download/bun-v{version}"
    for name in (ARCHIVE_NAME, "SHASUMS256.txt.asc"):
        checked_run(
            [
                "/usr/bin/curl", "--disable", "--fail", "--silent", "--show-error",
                "--proto", "=https", "--proto-redir", "=https", "--tlsv1.2",
                "--location", "--max-redirs", "3", "--connect-timeout", "30",
                "--max-time", "120", "--output", str(folder / name), f"{release}/{name}",
            ],
            environment, "release download", timeout=150,
        )
    verify_archive(folder, gpg, environment)
    binary_directory = folder / "bin"
    binary_directory.mkdir(mode=0o700)
    binary = binary_directory / "bun"
    with zipfile.ZipFile(folder / ARCHIVE_NAME) as archive:
        if archive.namelist().count(BINARY_MEMBER) != 1:
            raise Refusal("Bun archive must contain exactly one binary member")
        with archive.open(BINARY_MEMBER) as source, binary.open("xb") as destination:
            shutil.copyfileobj(source, destination)
    binary.chmod(0o555)
    reported = checked_run([str(binary), "--version"], environment, "version check")
    if reported.stdout.strip() != version:
        raise Refusal("Authenticated Bun version does not match the exact source pin")
    with output.open("a", encoding="utf-8") as handle:
        handle.write(str(binary_directory) + "\n")
    return binary


def main(argv=None, environ=None):
    arguments = sys.argv[1:] if argv is None else argv
    environment = os.environ if environ is None else environ
    if arguments:
        print("usage: setup_bun.py", file=sys.stderr)
        return 2
    required = ("BUN_VERSION", "RUNNER_TEMP", "GITHUB_PATH")
    if any(not environment.get(name) for name in (*required, "GITHUB_OUTPUT")):
        print("refused: Bun bootstrap runner inputs are missing", file=sys.stderr)
        return 2
    try:
        binary = setup_bun(*(environment[name] for name in required))
        digest = sha256_file(binary)
        with open(environment["GITHUB_OUTPUT"], "a", encoding="utf-8") as handle:
            handle.write(f"path={binary}\nsha256={digest}\n")
    except (Refusal, OSError, zipfile.BadZipFile) as error:
        reason = str(error) if isinstance(error, Refusal) else "Bun bootstrap file operation failed"
        print(f"refused: {reason}", file=sys.stderr)
        return 2
    print(f"Verified Bun {environment['BUN_VERSION']} with pinned signer {SIGNER_FINGERPRINT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
