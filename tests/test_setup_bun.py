import contextlib
import hashlib
import io
import os
import subprocess
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import support
import setup_bun
from common import Refusal


FINGERPRINT = "F3DCC08A8572C0749B3E18888EAB4D40A7B22B59"


class SetupBunTests(unittest.TestCase):
    def setUp(self):
        self.directory = support.TemporaryDirectory(dir=support.ROOT.parent / "fixer-t")
        self.base = self.directory.__enter__()
        self.addCleanup(self.directory.__exit__, None, None, None)
        self.runner_temp = self.base / "runner-temp"
        self.runner_temp.mkdir()
        self.path_output = self.base / "github-path"
        self.key = self.base / "public-key.asc"
        self.key.write_text("public-key stand-in\n", encoding="utf-8")
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            archive.writestr("bun-darwin-aarch64/bun", b"Bun stand-in, never executed\n")
            archive.writestr("bun-darwin-aarch64/unwanted-program", b"Must not be extracted\n")
        self.archive = stream.getvalue()
        self.digest = hashlib.sha256(self.archive).hexdigest()
        self.body = f"{self.digest}  bun-darwin-aarch64.zip\n"
        self.manifest = b"signed checksums stand-in\n"
        self.fingerprint = FINGERPRINT
        self.signer = FINGERPRINT
        self.status = "good"
        self.version = "1.4.4\n"
        self.events = []
        self.calls = []
        patches = [
            mock.patch.object(setup_bun, "KEY_FILE", self.key, create=True),
            mock.patch("setup_bun.shutil.which", return_value="/usr/bin/gpg"),
            mock.patch("setup_bun.subprocess.run", side_effect=self.program),
            mock.patch.dict(os.environ, {"TRACE_SENTINEL": "must-not-reach-a-child"}),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def program(self, args, **kwargs):
        self.calls.append((args, kwargs))
        if args[0] == "/usr/bin/curl":
            self.assertTrue(args[-1].startswith("https://github.com/oven-sh/bun/releases/download/bun-v1.4.4/"))
            destination = Path(args[args.index("--output") + 1])
            if args[-1].endswith("/bun-darwin-aarch64.zip"):
                destination.write_bytes(self.archive)
            elif args[-1].endswith("/SHASUMS256.txt.asc"):
                destination.write_bytes(self.manifest)
            else:
                raise AssertionError(f"Unexpected download: {args[-1]}")
            self.events.append("download")
            return subprocess.CompletedProcess(args, 0, "", "")
        if "--show-keys" in args:
            self.events.append("inspect-key")
            text = f"pub:-:255:22:8EAB4D40A7B22B59:0:0::-:::scSC:\nfpr:::::::::{self.fingerprint}:\n"
            return subprocess.CompletedProcess(args, 0, text, "")
        if "--import" in args:
            self.events.append("import-key")
            return subprocess.CompletedProcess(args, 0, "", "")
        if "--decrypt" in args:
            self.events.append("verify-signature")
            if Path(args[-1]).read_bytes() != b"signed checksums stand-in\n":
                return subprocess.CompletedProcess(args, 1, self.body, "[GNUPG:] BADSIG\n")
            status = f"[GNUPG:] GOODSIG 8EAB4D40A7B22B59 Robobun\n[GNUPG:] VALIDSIG {self.signer} 2026-10-22 1792705489 0 4 0 22 10 01 {self.signer}\n"
            if self.status != "good":
                status = ""
            return subprocess.CompletedProcess(args, 0, self.body, status)
        if Path(args[0]).name == "bun" and args[1:] == ["--version"]:
            self.events.append("bun")
            self.assertEqual(Path(args[0]).read_bytes(), b"Bun stand-in, never executed\n")
            return subprocess.CompletedProcess(args, 0, self.version, "")
        raise AssertionError(f"Unexpected process call: {args}")

    def bootstrap(self):
        return setup_bun.setup_bun("1.4.4", self.runner_temp, self.path_output)

    def assert_refused_before_bun(self):
        with self.assertRaises(Refusal):
            self.bootstrap()
        self.assertNotIn("bun", self.events)
        self.assertFalse(self.path_output.exists())

    def test_only_authenticated_binary_is_published_on_path(self):
        binary = self.bootstrap()
        self.assertEqual(self.path_output.read_text(), str(binary.parent) + "\n")
        self.assertEqual(list(binary.parent.iterdir()), [binary])
        self.assertLess(self.events.index("verify-signature"), self.events.index("bun"))
        self.assertEqual(self.events[-1], "bun")
        for args, kwargs in self.calls:
            self.assertNotIn("TRACE_SENTINEL", kwargs["env"])
            self.assertFalse(kwargs.get("shell", False))
            if args[0] == "/usr/bin/gpg":
                self.assertIn("--no-options", args)
                home = Path(kwargs["env"]["GNUPGHOME"])
                self.assertTrue(home.is_relative_to(self.runner_temp))
                self.assertEqual(home.stat().st_mode & 0o777, 0o700)

    def test_changed_archive_is_refused_before_bun_runs(self):
        self.archive += b"changed"
        self.assert_refused_before_bun()

    def test_changed_signed_manifest_is_refused_before_bun_runs(self):
        self.manifest = b"signed checksums stand-in changed\n"
        self.assert_refused_before_bun()

    def test_unsigned_manifest_is_refused_before_bun_runs(self):
        self.manifest = self.body.encode()
        self.assert_refused_before_bun()

    def test_wrong_signer_is_refused_before_bun_runs(self):
        self.signer = "0123456789ABCDEF0123456789ABCDEF01234567"
        self.assert_refused_before_bun()

    def test_wrong_public_key_is_refused_before_import(self):
        self.fingerprint = "0123456789ABCDEF0123456789ABCDEF01234567"
        self.assert_refused_before_bun()
        self.assertNotIn("import-key", self.events)

    def test_missing_valid_signature_status_is_refused_before_bun_runs(self):
        self.status = "missing"
        self.assert_refused_before_bun()

    def test_duplicate_archive_checksum_is_refused_before_bun_runs(self):
        self.body += self.body
        self.assert_refused_before_bun()

    def test_version_mismatch_does_not_publish_path(self):
        self.version = "9.9.9\n"
        with self.assertRaises(Refusal):
            self.bootstrap()
        self.assertFalse(self.path_output.exists())

    def test_nonexact_version_never_starts_a_process(self):
        for version in ("latest", "1.4", "1.4.4;echo unsafe", "1.4.4\n"):
            with self.subTest(version=version), self.assertRaises(Refusal):
                setup_bun.setup_bun(version, self.runner_temp, self.path_output)
        self.assertEqual(self.calls, [])

    def test_normal_entry_point_installs_the_pinned_version_and_reports_its_path_and_digest(self):
        outputs = self.runner_temp / "github-output"
        environment = {
            "BUN_VERSION": "1.4.4", "RUNNER_TEMP": str(self.runner_temp),
            "GITHUB_PATH": str(self.path_output), "GITHUB_OUTPUT": str(outputs),
        }
        self.assertEqual(setup_bun.main([], environ=environment), 0)
        self.assertTrue(self.path_output.exists())
        lines = dict(line.split("=", 1) for line in outputs.read_text(encoding="utf-8").splitlines())
        binary = Path(lines["path"])
        self.assertEqual(binary.parent, Path(self.path_output.read_text(encoding="utf-8").strip()))
        self.assertEqual(lines["sha256"], hashlib.sha256(binary.read_bytes()).hexdigest())

    def test_entry_point_without_a_step_output_file_refuses(self):
        environment = {
            "BUN_VERSION": "1.4.4", "RUNNER_TEMP": str(self.runner_temp),
            "GITHUB_PATH": str(self.path_output),
        }
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(setup_bun.main([], environ=environment), 2)
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
