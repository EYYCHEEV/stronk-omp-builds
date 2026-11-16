import contextlib
import hashlib
import io
import json
import os
import unittest
from unittest import mock

import support
from common import Refusal
from probe_runtime import PROBE_SOURCE, load_measured, main, measure

REVISION = "0123456789abcdef0123456789abcdef01234567"


def reported(**overrides):
    values = {"sqlite_version": "3.43.2", "bun_version": "1.4.4", "bun_revision": REVISION}
    values.update(overrides)
    return values


class ProbeRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.directory = support.TemporaryDirectory()
        self.base = self.directory.__enter__()
        self.addCleanup(self.directory.__exit__, None, None, None)
        self.source = support.make_source(self.base / "src")
        self.runner_temp = self.base / "runner-temp"
        self.runner_temp.mkdir()
        self.tools = self.base / "tools"
        self.seen = self.base / "seen"
        self.seen.mkdir()
        path = f"{self.tools}:/usr/bin:/bin"
        patcher = mock.patch.dict(os.environ, {"PATH": path, "ImageOS": "macos15", "BUN_VERSION": "1.4.4"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def stub(self, output, code=0, extra="", folder=None):
        printed = json.dumps(output) if isinstance(output, dict) else output
        body = "\n".join(
            [
                f'pwd > "{self.seen}/cwd"',
                f'env > "{self.seen}/env"',
                f'printf "%s\\n" "$@" > "{self.seen}/argv"',
                extra,
                f"printf '%s' '{printed}'",
                f"exit {code}",
            ]
        )
        return support.make_stub(folder or self.tools, "bun", body)

    def authenticated(self, bun=None):
        bun = self.tools / "bun" if bun is None else bun
        return {"BUN_PATH": str(bun), "BUN_SHA256": hashlib.sha256(bun.read_bytes()).hexdigest()}

    def measure(self):
        return measure(self.source, self.runner_temp, self.authenticated())

    def test_records_what_the_running_bun_reports_not_the_pins_or_the_environment(self):
        bun = self.stub(reported(sqlite_version="3.50.1"))
        measured = self.measure()
        self.assertEqual(
            measured,
            {
                "sqlite_version": "3.50.1",
                "bun_version": "1.4.4",
                "bun_revision": REVISION,
                "bun_sha256": hashlib.sha256(bun.read_bytes()).hexdigest(),
            },
        )

    def test_the_probe_runs_the_query_in_an_empty_folder_with_a_bare_environment(self):
        self.stub(reported())
        self.measure()
        self.assertEqual((self.seen / "argv").read_text(encoding="utf-8"), f"--eval\n{PROBE_SOURCE}\n")
        self.assertIn("select sqlite_version()", PROBE_SOURCE)
        cwd = self.runner_temp.resolve()
        ran_in = (self.seen / "cwd").read_text(encoding="utf-8").strip()
        self.assertTrue(ran_in.startswith(str(cwd) + "/runtime-probe-"), ran_in)
        self.assertNotIn(str(self.source), ran_in)
        names = {line.split("=", 1)[0] for line in (self.seen / "env").read_text(encoding="utf-8").splitlines()}
        self.assertTrue({"PATH", "HOME", "LC_ALL"} <= names)
        self.assertFalse({"ImageOS", "BUN_VERSION", "BUN_INSTALL", "BUN_OPTIONS"} & names)
        self.assertEqual(list(self.runner_temp.iterdir()), [])

    def test_refuses_output_it_cannot_trust(self):
        cases = {
            "nonzero exit": (reported(), 3, "exited 3"),
            "not json": ("not json", 0, "one JSON object"),
            "a list": ("[]", 0, "exactly sqlite_version"),
            "extra key": (reported(note="x"), 0, "exactly sqlite_version"),
            "missing key": ({"sqlite_version": "3.43.2", "bun_version": "1.4.4"}, 0, "exactly sqlite_version"),
            "short sqlite version": (reported(sqlite_version="3.43"), 0, "sqlite_version"),
            "sqlite version as a number": (reported(sqlite_version=3.43), 0, "sqlite_version"),
            "short revision": (reported(bun_revision="abc"), 0, "bun_revision"),
            "other bun": (reported(bun_version="1.4.5"), 0, "is not the source pin 1.4.4"),
        }
        for name, (output, code, message) in cases.items():
            with self.subTest(case=name):
                self.stub(output, code)
                with self.assertRaises(Refusal) as caught:
                    self.measure()
                self.assertIn(message, str(caught.exception))

    def test_refuses_a_bun_that_changes_while_it_runs(self):
        self.stub(reported(), extra='printf "\\n" >> "$0"')
        with self.assertRaises(Refusal) as caught:
            self.measure()
        self.assertIn("changed while", str(caught.exception))

    def test_refuses_a_missing_bun_or_one_inside_the_source(self):
        with self.assertRaises(Refusal):
            measure(self.source, self.runner_temp, {})
        with self.assertRaises(Refusal):
            measure(self.source, self.runner_temp, {"BUN_PATH": str(self.tools / "bun"), "BUN_SHA256": "0" * 64})
        inside = self.stub(reported(), folder=self.source / "bin")
        with self.assertRaises(Refusal) as caught:
            measure(self.source, self.runner_temp, self.authenticated(inside))
        self.assertIn("inside the source checkout", str(caught.exception))

    def test_a_different_bun_added_to_path_is_never_measured(self):
        self.stub(reported())
        injected = self.base / "injected"
        self.stub(reported(sqlite_version="3.99.9"), folder=injected)
        with mock.patch.dict(os.environ, {"PATH": f"{injected}:{self.tools}:/usr/bin:/bin"}):
            measured = self.measure()
        self.assertEqual(measured["sqlite_version"], "3.43.2")
        self.assertEqual(measured["bun_sha256"], hashlib.sha256((self.tools / "bun").read_bytes()).hexdigest())

    def test_refuses_a_bun_that_is_not_the_authenticated_digest(self):
        self.stub(reported())
        environ = self.authenticated()
        self.stub(reported(sqlite_version="3.99.9"))
        with self.assertRaises(Refusal) as caught:
            measure(self.source, self.runner_temp, environ)
        self.assertIn("not the authenticated", str(caught.exception))
        self.assertFalse((self.seen / "argv").exists())

    def test_main_writes_the_measurement_once_and_refuses_with_exit_code_two(self):
        self.stub(reported())
        output = self.base / "probe.json"
        environment = {"RUNNER_TEMP": str(self.runner_temp), **self.authenticated()}
        arguments = ["--source", str(self.source), "--output", str(output)]
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(arguments, environment), 0)
            self.assertEqual(load_measured(output)["sqlite_version"], "3.43.2")
            self.assertEqual(main(arguments, environment), 2)
            self.assertEqual(main(["--source", str(self.source), "--output", str(self.base / "x.json")], {}), 2)
            self.assertEqual(
                main(["--source", str(self.source), "--output", str(self.base / "z.json")], {"RUNNER_TEMP": str(self.runner_temp)}),
                2,
            )
            self.stub(reported(bun_version="1.4.5"))
            environment = {"RUNNER_TEMP": str(self.runner_temp), **self.authenticated()}
            self.assertEqual(main(["--source", str(self.source), "--output", str(self.base / "y.json")], environment), 2)
        self.assertFalse((self.base / "z.json").exists())
        self.assertFalse((self.base / "y.json").exists())

    def test_load_measured_refuses_a_malformed_file(self):
        path = self.base / "probe.json"
        good = {**reported(), "bun_sha256": "a" * 64}
        for content in ("not json", "[]", json.dumps({**good, "bun_sha256": "A" * 64}), json.dumps({**good, "x": 1})):
            with self.subTest(content=content):
                path.write_text(content, encoding="utf-8")
                with self.assertRaises(Refusal):
                    load_measured(path)
        with self.assertRaises(Refusal):
            load_measured(self.base / "missing.json")


if __name__ == "__main__":
    unittest.main()
