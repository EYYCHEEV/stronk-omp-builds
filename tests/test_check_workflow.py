import contextlib
import io
import unittest

import support
from check_workflow import check, main

REAL = support.WORKFLOW.read_text(encoding="utf-8")
CHECKOUT = "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"

MUTATIONS = {
    "tag instead of sha": (CHECKOUT, "actions/checkout@v7", "is not owner/repo@<40 hex commit>"),
    "short sha": (CHECKOUT, "actions/checkout@3d3c42e", "is not owner/repo@<40 hex commit>"),
    "upper case sha": (CHECKOUT, "actions/checkout@3D3C42E5AAC5BA805825DA76410C181273BA90B1", "is not owner/repo@<40 hex commit>"),
    "local action": (CHECKOUT, "./.github/actions/x", "is not owner/repo@<40 hex commit>"),
    "docker action": (CHECKOUT, "docker://alpine:3", "is not owner/repo@<40 hex commit>"),
    "extra trigger": ("on:\n  workflow_dispatch:", "on:\n  push:\n  workflow_dispatch:", "expected only workflow_dispatch"),
    "pull request trigger": ("on:\n  workflow_dispatch:", "on:\n  pull_request:\n  workflow_dispatch:", "expected only workflow_dispatch"),
    "schedule trigger": ("on:\n  workflow_dispatch:", "on:\n  schedule:\n    - cron: '0 0 * * *'\n  workflow_dispatch:", "expected only workflow_dispatch"),
    "other trigger only": ("on:\n  workflow_dispatch:", "on:\n  workflow_call:", "expected only workflow_dispatch"),
    "top level permissions write": ("permissions: {}\n\ndefaults:", "permissions:\n  contents: write\n\ndefaults:", "top-level permissions"),
    "top level permissions read-all": ("permissions: {}\n\ndefaults:", "permissions: read-all\n\ndefaults:", "top-level permissions"),
    "top level permissions missing": ("permissions: {}\n\ndefaults:", "defaults:", "top-level permissions"),
    "build contents write": ("    permissions:\n      contents: read\n    env:", "    permissions:\n      contents: write\n    env:", "permission contents: write"),
    "build extra permission": ("    permissions:\n      contents: read\n    env:", "    permissions:\n      contents: read\n      actions: read\n    env:", "permission actions: read"),
    "build id-token": ("    permissions:\n      contents: read\n    env:", "    permissions:\n      contents: read\n      id-token: write\n    env:", "permission id-token: write"),
    "attest packages": ("      attestations: write\n    steps:", "      attestations: write\n      packages: write\n    steps:", "permission packages: write"),
    "attest artifact-metadata": ("      attestations: write\n    steps:", "      attestations: write\n      artifact-metadata: write\n    steps:", "permission artifact-metadata: write"),
    "attest read-all": ("    permissions:\n      contents: read\n      id-token: write\n      attestations: write\n", "    permissions: write-all\n", "permissions must be an explicit mapping"),
    "expression inside run": ("run: python3 builder/scripts/validate_inputs.py", 'run: python3 builder/scripts/validate_inputs.py "${{ inputs.tag }}"', "expression inside run:"),
    "build on larger runner": ("runs-on: macos-15\n", "runs-on: macos-15-xlarge\n", "runs-on is 'macos-15-xlarge'"),
    "build on macos-latest": ("runs-on: macos-15\n", "runs-on: macos-latest\n", "runs-on is 'macos-latest'"),
    "build on self hosted": ("runs-on: macos-15\n", "runs-on: self-hosted\n", "runs-on is 'self-hosted'"),
    "attest on self hosted": ("runs-on: ubuntu-24.04", "runs-on: self-hosted", "runs-on is 'self-hosted'"),
    "attest on macos": ("runs-on: ubuntu-24.04", "runs-on: macos-15", "runs-on is 'macos-15'"),
    "attest on larger ubuntu": ("runs-on: ubuntu-24.04", "runs-on: ubuntu-24.04-8core", "runs-on is 'ubuntu-24.04-8core'"),
    "persisted credentials": ("          path: builder\n          persist-credentials: false", "          path: builder", "persist-credentials"),
    "missing timeout": ("    timeout-minutes: 150\n", "", "timeout-minutes"),
    "timeout at the cap": ("timeout-minutes: 150", "timeout-minutes: 360", "timeout-minutes"),
    "secret reference": ("TESTS: ${{ inputs.tests }}", "TESTS: ${{ secrets.TOKEN }}", "refers to secrets"),
    "yaml comment": ("permissions: {}\n", "permissions: {}\n# a note\n", "holds a comment"),
    "trailing comment": ("timeout-minutes: 150", "timeout-minutes: 150 # long", "holds a comment"),
    "extra job": ("jobs:\n", "jobs:\n  extra:\n    runs-on: ubuntu-24.04\n    steps: []\n", "expected build and attest"),
}


class CheckWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.directory = support.TemporaryDirectory()
        self.base = self.directory.__enter__()
        self.addCleanup(self.directory.__exit__, None, None, None)

    def write(self, text):
        path = self.base / "mac-build.yml"
        path.write_text(text, encoding="utf-8")
        return path

    def test_the_real_workflow_passes(self):
        self.assertEqual(check(support.WORKFLOW), [])

    def test_each_mutation_fails_with_its_own_reason(self):
        for name, (old, new, expected) in MUTATIONS.items():
            with self.subTest(name=name):
                self.assertIn(old, REAL)
                mutated = REAL.replace(old, new, 1)
                self.assertNotEqual(mutated, REAL)
                violations = check(self.write(mutated))
                self.assertTrue(any(expected in violation for violation in violations), violations)

    def test_invalid_yaml_fails(self):
        self.assertTrue(check(self.write("on: [\n")))
        self.assertTrue(check(self.write("just text")))

    def test_main_exit_codes(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main([str(support.WORKFLOW)]), 0)
        self.assertIn("OK", output.getvalue())
        broken = self.write(REAL.replace(CHECKOUT, "actions/checkout@v7", 1))
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main([str(broken)]), 1)
        self.assertIn("FAIL", output.getvalue())
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main([]), 2)


if __name__ == "__main__":
    unittest.main()
