import copy
import contextlib
import io
import unittest

import yaml

import support
from check_workflow import check, main

REAL = support.WORKFLOW.read_text(encoding="utf-8")
CHECKOUT = "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
AUTHENTICATE_BUN = "python3 builder/scripts/setup_bun.py"

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
    "extra job": ("jobs:\n", "jobs:\n  extra:\n    runs-on: ubuntu-24.04\n    steps: []\n", "expected build, attest and precheck"),
    "runtime probe removed": (
        "      - name: Measure runtime\n        env:\n          BUN_PATH: ${{ steps.bun.outputs.path }}\n"
        "          BUN_SHA256: ${{ steps.bun.outputs.sha256 }}\n"
        '        run: python3 builder/scripts/probe_runtime.py --source src --output "$RUNNER_TEMP/runtime-probe.json"\n\n',
        "",
        "runtime measurement must run exactly once",
    ),
    "runtime probe resolves bun from PATH": (
        "      - name: Measure runtime\n        env:\n          BUN_PATH: ${{ steps.bun.outputs.path }}\n"
        "          BUN_SHA256: ${{ steps.bun.outputs.sha256 }}\n",
        "      - name: Measure runtime\n",
        "runtime measurement must use exactly the authenticated Bun",
    ),
    "tests resolve bun from PATH": (
        "          TESTS: ${{ inputs.tests }}\n          BUN_PATH: ${{ steps.bun.outputs.path }}\n"
        "          BUN_SHA256: ${{ steps.bun.outputs.sha256 }}\n",
        "          TESTS: ${{ inputs.tests }}\n",
        "the tests must run with exactly the tests input and the authenticated Bun",
    ),
    "bun step reports no outputs": (
        "      - name: Authenticate Bun release\n        id: bun\n",
        "      - name: Authenticate Bun release\n",
        "must report as step bun",
    ),
    "mode defaults to precheck": ("        default: release\n", "        default: precheck\n", "workflow_dispatch inputs must be exactly"),
    "dispatch id input removed": (
        "      dispatch_id:\n        description: 32 lowercase hex characters, new for every dispatch, precheck mode only\n"
        "        required: false\n        type: string\n",
        "",
        "workflow_dispatch inputs must be exactly",
    ),
    "run-name drops the dispatch id": (
        "format('precheck {0} {1} {2}', inputs.commit, inputs.tests_sha256, inputs.dispatch_id)",
        "format('precheck {0} {1}', inputs.commit, inputs.tests_sha256)",
        "run-name must carry",
    ),
    "precheck builds the release binary with a disguised command": (
        "        run: bun run build:native\n\n      - name: Run tests\n",
        "        run: bun run build:native\n\n      - name: Bundle\n        run: cd src/packages/coding-agent && bun run build\n\n"
        "      - name: Run tests\n",
        "precheck step 9 is not the allowlisted step 'Run tests'",
    ),
    "precheck record written without the dispatch id": (
        "          DISPATCH_ID: ${{ inputs.dispatch_id }}\n        run: python3 builder/scripts/manifest.py precheck",
        "        run: python3 builder/scripts/manifest.py precheck",
        "is not the allowlisted step 'Write pre-check record'",
    ),
    "runtime probe skipped": ("      - name: Measure runtime\n", "      - name: Measure runtime\n        if: false\n", "runtime measurement cannot be skipped"),
    "runtime probe softened": ("      - name: Measure runtime\n", "      - name: Measure runtime\n        continue-on-error: true\n", "runtime measurement cannot be skipped"),
    "runtime probe redirected": ("      - name: Measure runtime\n", "      - name: Measure runtime\n        working-directory: src\n", "runtime measurement cannot be skipped"),
    "manifest written without the probe": (' --probe "$RUNNER_TEMP/runtime-probe.json"', "", "build manifest must be written once"),
    "build runs in every mode": ("    name: build darwin-arm64\n    if: ${{ inputs.mode == 'release' }}\n", "    name: build darwin-arm64\n", "job build must run only when"),
    "attest runs in precheck": ("    name: attest\n    if: ${{ inputs.mode == 'release' }}\n", "    name: attest\n    if: ${{ inputs.mode == 'precheck' }}\n", "job attest must run only when"),
    "precheck runs in release": ("if: ${{ inputs.mode == 'precheck' }}", "if: ${{ inputs.mode == 'release' }}", "job precheck must run only when"),
    "precheck checks out the tag": ("          ref: ${{ inputs.commit }}\n", "          ref: refs/tags/${{ inputs.tag }}\n", "check out the fork once, at the full input commit"),
    "precheck uploads more": ("          path: precheck.json\n", "          path: |\n            precheck.json\n            src\n", "upload only precheck.json"),
    "precheck uploads under the release name": ("          name: mac-precheck\n", "          name: mac-build\n", "upload only precheck.json"),
    "precheck signs a binary": (
        "--output precheck.json\n",
        "--output precheck.json\n\n      - name: Sign\n        run: codesign --verify out/omp-darwin-arm64\n",
        "must not package or sign a binary",
    ),
    "precheck builds the release binary": (
        "        run: bun run build:native\n\n      - name: Run tests\n",
        "        run: bun run build:native\n\n      - name: Build binary\n        working-directory: src\n"
        "        run: bun --cwd=packages/coding-agent run build\n\n      - name: Run tests\n",
        "must not package or sign a binary",
    ),
    "precheck writes no record from the probe": (' --probe "$RUNNER_TEMP/runtime-probe.json" --output precheck.json', " --output precheck.json", "build manifest must be written once"),
    "precheck attests": (
        "      - name: Upload pre-check record\n",
        "      - name: Attest\n        uses: actions/attest@1e69f48acb82d1966a394da916b4c1698aa569d6\n        with:\n          subject-path: precheck.json\n\n      - name: Upload pre-check record\n",
        "must not use actions/attest",
    ),
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

    def test_removing_bun_authentication_is_refused(self):
        document = yaml.safe_load(REAL)
        steps = document["jobs"]["build"]["steps"]
        document["jobs"]["build"]["steps"] = [
            step for step in steps if step.get("run") != AUTHENTICATE_BUN
        ]
        violations = check(self.write(yaml.safe_dump(document)))
        self.assertTrue(any("Bun authentication" in item for item in violations), violations)

    def test_bun_authentication_cannot_be_skipped_or_retargeted(self):
        original = yaml.safe_load(REAL)
        steps = original["jobs"]["build"]["steps"]
        indexes = [index for index, step in enumerate(steps) if step.get("run") == AUTHENTICATE_BUN]
        self.assertEqual(len(indexes), 1)
        index = indexes[0]
        for field, value in (
            ("if", False),
            ("continue-on-error", True),
            ("run", "true"),
            ("env", {"BUN_VERSION": "latest"}),
            ("working-directory", "src"),
        ):
            with self.subTest(field=field):
                document = copy.deepcopy(original)
                document["jobs"]["build"]["steps"][index][field] = value
                violations = check(self.write(yaml.safe_dump(document)))
                self.assertTrue(any("Bun authentication" in item for item in violations), violations)
        document = copy.deepcopy(original)
        moved = document["jobs"]["build"]["steps"].pop(index)
        document["jobs"]["build"]["steps"].append(moved)
        violations = check(self.write(yaml.safe_dump(document)))
        self.assertTrue(any("Bun authentication" in item for item in violations), violations)

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

    def test_the_runtime_measurement_must_sit_between_the_tests_and_the_manifest(self):
        original = yaml.safe_load(REAL)
        steps = original["jobs"]["build"]["steps"]
        index = next(position for position, step in enumerate(steps) if step.get("name") == "Measure runtime")
        document = copy.deepcopy(original)
        moved = document["jobs"]["build"]["steps"].pop(index)
        document["jobs"]["build"]["steps"].append(moved)
        violations = check(self.write(yaml.safe_dump(document)))
        self.assertTrue(any("immediately follow the tests" in item for item in violations), violations)
        self.assertTrue(any("after it" in item for item in violations), violations)

    def test_the_precheck_job_takes_no_extra_environment_or_steps(self):
        original = yaml.safe_load(REAL)
        document = copy.deepcopy(original)
        document["jobs"]["precheck"]["env"]["BUN_OPTIONS"] = "--preload=./evil.ts"
        violations = check(self.write(yaml.safe_dump(document)))
        self.assertTrue(any("precheck job env must be exactly" in item for item in violations), violations)
        document = copy.deepcopy(original)
        document["jobs"]["precheck"]["steps"].append({"name": "Extra", "run": "true"})
        violations = check(self.write(yaml.safe_dump(document)))
        self.assertTrue(any("the allowlisted sequence has 13" in item for item in violations), violations)
        document = copy.deepcopy(original)
        document["jobs"]["precheck"]["steps"][7]["run"] = "bun install"
        violations = check(self.write(yaml.safe_dump(document)))
        self.assertTrue(any("precheck step 7 is not the allowlisted step 'Install dependencies'" in item for item in violations), violations)

    def test_a_custom_workflow_shell_is_refused(self):
        for shell in ("sh -c 'id; exec bash \"$0\"' {0}", "python3 {0}", "bash --noprofile --norc -eo pipefail {0}"):
            with self.subTest(shell=shell):
                document = yaml.safe_load(REAL)
                document["defaults"]["run"]["shell"] = shell
                violations = check(self.write(yaml.safe_dump(document)))
                self.assertEqual(len(violations), 1, violations)
                self.assertIn("workflow defaults are", violations[0])

    def test_workflow_defaults_take_nothing_but_the_bash_shell(self):
        for defaults in (
            {"run": {"shell": "bash", "working-directory": "src"}},
            {"run": {}},
            {},
            None,
        ):
            with self.subTest(defaults=defaults):
                document = yaml.safe_load(REAL)
                document["defaults"] = defaults
                violations = check(self.write(yaml.safe_dump(document)))
                self.assertTrue(any("workflow defaults are" in item for item in violations), violations)
        document = yaml.safe_load(REAL)
        del document["defaults"]
        violations = check(self.write(yaml.safe_dump(document)))
        self.assertTrue(any("workflow defaults are" in item for item in violations), violations)
        self.assertTrue(any("workflow keys are" in item for item in violations), violations)

    def test_no_workflow_level_env_or_other_keys(self):
        for key, value in (
            ("env", {"BASH_ENV": "./src/evil.sh"}),
            ("env", {}),
            ("concurrency", {"group": "x"}),
        ):
            with self.subTest(key=key, value=value):
                document = yaml.safe_load(REAL)
                document[key] = value
                violations = check(self.write(yaml.safe_dump(document)))
                self.assertTrue(any("workflow keys are" in item for item in violations), violations)
        document = yaml.safe_load(REAL)
        document["name"] = "other"
        violations = check(self.write(yaml.safe_dump(document)))
        self.assertTrue(any("workflow name is 'other'" in item for item in violations), violations)

    def test_no_job_level_defaults_container_or_services(self):
        for job in ("build", "attest", "precheck"):
            for key, value in (
                ("defaults", {"run": {"shell": "python3 {0}"}}),
                ("container", {"image": "alpine:3"}),
                ("services", {"helper": {"image": "alpine:3"}}),
            ):
                with self.subTest(job=job, key=key):
                    document = yaml.safe_load(REAL)
                    document["jobs"][job][key] = value
                    violations = check(self.write(yaml.safe_dump(document)))
                    self.assertTrue(any(f"job {job} keys are" in item for item in violations), violations)

    def test_duplicate_and_ambiguously_typed_keys_are_refused(self):
        self.assertIn("\non:\n", REAL)
        self.assertIn("\ndefaults:\n", REAL)
        self.assertTrue(REAL.endswith("\n"))
        cases = {
            "string and boolean on": REAL.replace("\non:\n", "\n!!str on:\n", 1) + "on:\n  push:\n",
            "two spellings of on": REAL.replace("\non:\n", "\nOn:\n  push:\non:\n", 1),
            "repeated defaults": REAL.replace("\ndefaults:\n", "\ndefaults:\n  run:\n    shell: python3 {0}\n\ndefaults:\n", 1),
            "repeated job key": REAL.replace("\n    timeout-minutes:", "\n    timeout-minutes: 1\n    timeout-minutes:", 1),
        }
        for name, text in cases.items():
            with self.subTest(name=name):
                self.assertNotEqual(text, REAL)
                violations = check(self.write(text))
                self.assertTrue(any("duplicate key" in item for item in violations), violations)


if __name__ == "__main__":
    unittest.main()
