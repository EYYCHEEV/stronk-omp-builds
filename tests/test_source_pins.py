import contextlib
import io
import unittest

import support
from common import Refusal
from source_pins import main, read_pins


class SourcePinTests(unittest.TestCase):
    def setUp(self):
        self.directory = support.TemporaryDirectory()
        self.base = self.directory.__enter__()
        self.addCleanup(self.directory.__exit__, None, None, None)

    def test_a_floor_or_exact_package_manager_means_exactly_that_version(self):
        for package_manager in ("bun@>=1.4.4", "bun@1.4.4"):
            with self.subTest(package_manager=package_manager):
                source = support.make_source(self.base / "s", package_manager=package_manager)
                self.assertEqual(read_pins(source), {"bun": "1.4.4", "rust": "nightly-2026-10-06", "omp": "18.14.2"})

    def test_refuses_loose_package_managers(self):
        for package_manager in ("bun@^1.4.4", "bun@latest", "bun@1.4", "bun@1.4.4+sha512.abc", "npm@10.0.0", "bun@>1.4.4", ""):
            with self.subTest(package_manager=package_manager):
                source = support.make_source(self.base / "s", package_manager=package_manager)
                with self.assertRaises(Refusal):
                    read_pins(source)

    def test_accepts_only_exact_rust_channels(self):
        for channel in ("nightly-2026-10-06", "1.90.0"):
            with self.subTest(channel=channel):
                source = support.make_source(self.base / "s", channel=channel)
                self.assertEqual(read_pins(source)["rust"], channel)
        for channel in ("nightly", "stable", "beta", "1.90", "nightly-2026-10", "nightly-2026-10-06-x", "1.90.0\\nx"):
            with self.subTest(channel=channel):
                source = support.make_source(self.base / "s", channel=channel)
                with self.assertRaises(Refusal):
                    read_pins(source)

    def test_refuses_a_bad_coding_agent_version(self):
        for version in ("18.14", "v18.14.2", "18.14.2-beta.1", ""):
            with self.subTest(version=version):
                source = support.make_source(self.base / "s", version=version)
                with self.assertRaises(Refusal):
                    read_pins(source)

    def test_refuses_missing_files(self):
        with self.assertRaises(Refusal):
            read_pins(self.base / "empty")

    def test_main_prints_and_appends_to_the_output_file(self):
        source = support.make_source(self.base / "s")
        output = self.base / "github-output"
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = main(["--source", str(source)], {"GITHUB_OUTPUT": str(output)})
        self.assertEqual(code, 0)
        expected = "bun=1.4.4\nrust=nightly-2026-10-06\nomp=18.14.2\n"
        self.assertEqual(stdout.getvalue(), expected)
        self.assertEqual(output.read_text(encoding="utf-8"), expected)

    def test_main_refuses_with_exit_code_two(self):
        source = support.make_source(self.base / "s", package_manager="bun@latest")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["--source", str(source)], {}), 2)


if __name__ == "__main__":
    unittest.main()
