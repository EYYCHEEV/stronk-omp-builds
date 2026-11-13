import os
import unittest
from unittest import mock

import support
from common import Refusal
from smoke import smoke


class SmokeTests(unittest.TestCase):
    def setUp(self):
        self.directory = support.TemporaryDirectory()
        self.base = self.directory.__enter__()
        self.addCleanup(self.directory.__exit__, None, None, None)
        self.trace = self.base / "trace.txt"

    def binary(self, version_output="omp/1.2.3", version_code=0, smoke_code=0):
        body = (
            'case "$1" in\n'
            f'  --version) echo "{version_output}"; exit {version_code} ;;\n'
            f'  --smoke-test) printf "%s\\n%s\\n" "$HOME" "${{LEAK_PROBE:-unset}}" > \'{self.trace}\'; exit {smoke_code} ;;\n'
            "esac\n"
            "exit 9"
        )
        return support.make_stub(self.base, "omp-stub", body)

    def test_passes_in_a_throwaway_home_with_a_minimal_environment(self):
        binary = self.binary()
        with mock.patch.dict(os.environ, {"LEAK_PROBE": "secret"}):
            smoke(str(binary), "1.2.3")
        home, leak = self.trace.read_text(encoding="utf-8").splitlines()
        self.assertNotEqual(home, os.environ.get("HOME"))
        self.assertFalse(os.path.exists(home))
        self.assertEqual(leak, "unset")

    def test_refuses_a_version_mismatch(self):
        with self.assertRaises(Refusal):
            smoke(str(self.binary(version_output="omp/9.9.9")), "1.2.3")
        with self.assertRaises(Refusal):
            smoke(str(self.binary(version_output="1.2.3")), "1.2.3")
        with self.assertRaises(Refusal):
            smoke(str(self.binary(version_output="omp/1.2.3 extra")), "1.2.3")

    def test_refuses_failing_commands(self):
        with self.assertRaises(Refusal):
            smoke(str(self.binary(version_code=1)), "1.2.3")
        with self.assertRaises(Refusal):
            smoke(str(self.binary(smoke_code=1)), "1.2.3")


if __name__ == "__main__":
    unittest.main()
