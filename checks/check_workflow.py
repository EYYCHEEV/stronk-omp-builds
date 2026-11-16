import re
import sys
from pathlib import Path

import yaml

FULL_SHA_USES = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_./-]+)?@[0-9a-f]{40}")
UBUNTU_LABEL = re.compile(r"ubuntu-(?:latest|[0-9]{2}\.[0-9]{2})")
COMMENT = re.compile(r"(?:^|\s)#")
SECRET_REFERENCE = re.compile(r"secrets\s*[.\[]", re.IGNORECASE)

JOB_PERMISSIONS = {
    "build": {"contents": "read"},
    "attest": {"contents": "read", "id-token": "write", "attestations": "write"},
    "precheck": {"contents": "read"},
}
JOB_MODES = {"build": "release", "attest": "release", "precheck": "precheck"}
WORKFLOW_KEYS = {"name", "run-name", "on", "permissions", "defaults", "jobs"}
WORKFLOW_DEFAULTS = {"run": {"shell": "bash"}}
MAC_JOB_KEYS = {"name", "if", "runs-on", "timeout-minutes", "permissions", "env", "steps"}
JOB_KEYS = {
    "build": MAC_JOB_KEYS,
    "attest": {"name", "if", "needs", "runs-on", "timeout-minutes", "permissions", "steps"},
    "precheck": MAC_JOB_KEYS,
}

RUN_NAME = (
    "mac-build ${{ inputs.mode == 'precheck' && format('precheck {0} {1} {2}', inputs.commit, inputs.tests_sha256,"
    " inputs.dispatch_id) || inputs.tag }}"
)
INPUTS = {
    "mode": {
        "description": "release builds, attests and uploads the binary; precheck only runs the tests at the commit",
        "required": False,
        "default": "release",
        "type": "choice",
        "options": ["release", "precheck"],
    },
    "tag": {
        "description": "Fork tag to build, stronk-omp-YYYYMMDDTHHMMSSZ-N, release mode only",
        "required": False,
        "type": "string",
    },
    "commit": {
        "description": "Full 40 character lowercase commit the tag must point at",
        "required": True,
        "type": "string",
    },
    "tests": {
        "description": "JSON list of argv lists, each one bun test or cargo test with plain arguments",
        "required": True,
        "type": "string",
    },
    "tests_sha256": {
        "description": "sha256 of the tests input, precheck mode only",
        "required": False,
        "type": "string",
    },
    "dispatch_id": {
        "description": "32 lowercase hex characters, new for every dispatch, precheck mode only",
        "required": False,
        "type": "string",
    },
}
BUN_STEP_ENV = {"BUN_PATH": "${{ steps.bun.outputs.path }}", "BUN_SHA256": "${{ steps.bun.outputs.sha256 }}"}
TESTS_ENV = {"TESTS": "${{ inputs.tests }}", **BUN_STEP_ENV}


def trigger_names(document):
    if "on" in document:
        triggers = document["on"]
    elif True in document:
        triggers = document[True]
    else:
        return None
    if isinstance(triggers, str):
        return {triggers}
    if isinstance(triggers, list):
        return set(map(str, triggers))
    if isinstance(triggers, dict):
        return set(map(str, triggers))
    return None


def walk_uses(node, found):
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "uses":
                found.append(value)
            else:
                walk_uses(value, found)
    elif isinstance(node, list):
        for item in node:
            walk_uses(item, found)


def check_runs_on(name, job):
    runs_on = job.get("runs-on")
    if name in ("build", "precheck"):
        return [] if runs_on == "macos-15" else [f"job {name} runs-on is {runs_on!r}, expected 'macos-15'"]
    if name == "attest":
        if isinstance(runs_on, str) and UBUNTU_LABEL.fullmatch(runs_on):
            return []
        return [f"job attest runs-on is {runs_on!r}, expected a standard Ubuntu label"]
    return []


def check_job_permissions(name, job):
    allowed = JOB_PERMISSIONS[name]
    granted = job.get("permissions")
    if not isinstance(granted, dict) or not granted:
        return [f"job {name} permissions must be an explicit mapping, got {granted!r}"]
    return [
        f"job {name} permission {key}: {value} is not allowed"
        for key, value in granted.items()
        if allowed.get(key) != value
    ]


def check_bun_authentication(job):
    steps = job.get("steps") or []
    if not isinstance(steps, list) or any(not isinstance(step, dict) for step in steps):
        return ["Bun authentication requires a build steps list"]
    indexes = [
        index for index, step in enumerate(steps)
        if step.get("run") == "python3 builder/scripts/setup_bun.py"
    ]
    if len(indexes) != 1:
        return ["Bun authentication must run exactly once before source builds"]
    index = indexes[0]
    authentication = steps[index]
    violations = []
    if set(authentication) - {"name", "id", "run", "env"} or authentication.get("id") != "bun":
        violations.append("Bun authentication cannot be skipped, softened or redirected, and must report as step bun")
    if authentication.get("env") != {"BUN_VERSION": "${{ steps.pins.outputs.bun }}"}:
        violations.append("Bun authentication must use exactly the source-pins version")
    pins = [
        position for position, step in enumerate(steps)
        if step.get("id") == "pins"
        and step.get("run") == "python3 builder/scripts/source_pins.py --source src"
    ]
    if len(pins) != 1 or index != pins[0] + 1:
        violations.append("Bun authentication must immediately follow source pins")
    if any(str(step.get("uses", "")).startswith("oven-sh/setup-bun@") for step in steps):
        violations.append("Bun authentication cannot be replaced by an unauthenticated setup action")
    return violations


PROBE_RUN = 'python3 builder/scripts/probe_runtime.py --source src --output "$RUNNER_TEMP/runtime-probe.json"'
TESTS_RUN = 'python3 builder/scripts/run_tests.py --repo src --results "$RUNNER_TEMP/test-results.json"'
MANIFEST_RUN = (
    "python3 builder/scripts/manifest.py write --source src --binary out/omp-darwin-arm64"
    ' --tests "$RUNNER_TEMP/test-results.json" --probe "$RUNNER_TEMP/runtime-probe.json"'
    " --output out/build-manifest.json"
)
PRECHECK_RECORD_RUN = (
    'python3 builder/scripts/manifest.py precheck --tests "$RUNNER_TEMP/test-results.json"'
    ' --probe "$RUNNER_TEMP/runtime-probe.json" --output precheck.json'
)
PRECHECK_UPLOAD = {"name": "mac-precheck", "path": "precheck.json", "if-no-files-found": "error", "retention-days": 7}
RELEASE_ONLY_RUNS = ("codesign", "lipo", "build-manifest", "dist/omp", "packages/coding-agent run build", "smoke.py")
CHECKOUT_USES = "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
UPLOAD_USES = "actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9"
RUST_RUN = (
    "set -euo pipefail\n"
    'rustup toolchain install "$CHANNEL" --profile minimal --no-self-update\n'
    'echo "RUSTUP_TOOLCHAIN=$CHANNEL" >> "$GITHUB_ENV"\n'
    'active="$(RUSTUP_TOOLCHAIN="$CHANNEL" rustup show active-toolchain)"\n'
    'case "$active" in\n'
    '  "$CHANNEL"-aarch64-apple-darwin*) ;;\n'
    "  *)\n"
    '    echo "active toolchain is $active, source pins $CHANNEL" >&2\n'
    "    exit 1\n"
    "    ;;\n"
    "esac\n"
)
PRECHECK_JOB_ENV = {"RUSTUP_AUTO_INSTALL": "0", "OMP_NATIVE_BUILD_BACKEND": "cargo", "CARGO_INCREMENTAL": "0"}
PRECHECK_STEPS = (
    {"name": "Check out builder", "uses": CHECKOUT_USES, "with": {"path": "builder", "persist-credentials": False}},
    {
        "name": "Validate inputs",
        "env": {
            "EVENT": "${{ github.event_name }}",
            "REF": "${{ github.ref }}",
            "REPOSITORY": "${{ github.repository }}",
            "TAG": "${{ inputs.tag }}",
            "COMMIT": "${{ inputs.commit }}",
            "TESTS": "${{ inputs.tests }}",
            "MODE": "${{ inputs.mode }}",
            "TESTS_SHA256": "${{ inputs.tests_sha256 }}",
            "DISPATCH_ID": "${{ inputs.dispatch_id }}",
        },
        "run": "python3 builder/scripts/validate_inputs.py",
    },
    {
        "name": "Check out fork commit",
        "uses": CHECKOUT_USES,
        "with": {
            "repository": "EYYCHEEV/oh-my-pi", "ref": "${{ inputs.commit }}", "path": "src", "persist-credentials": False,
        },
    },
    {
        "name": "Verify commit",
        "env": {"MODE": "${{ inputs.mode }}", "COMMIT": "${{ inputs.commit }}"},
        "run": "python3 builder/scripts/verify_checkout.py src",
    },
    {"name": "Read source pins", "id": "pins", "run": "python3 builder/scripts/source_pins.py --source src"},
    {
        "name": "Authenticate Bun release",
        "id": "bun",
        "env": {"BUN_VERSION": "${{ steps.pins.outputs.bun }}"},
        "run": "python3 builder/scripts/setup_bun.py",
    },
    {"name": "Install Rust toolchain", "env": {"CHANNEL": "${{ steps.pins.outputs.rust }}"}, "run": RUST_RUN},
    {"name": "Install dependencies", "working-directory": "src", "run": "bun install --frozen-lockfile"},
    {"name": "Build natives", "working-directory": "src", "run": "bun run build:native"},
    {"name": "Run tests", "env": TESTS_ENV, "run": TESTS_RUN},
    {"name": "Measure runtime", "env": BUN_STEP_ENV, "run": PROBE_RUN},
    {
        "name": "Write pre-check record",
        "env": {"COMMIT": "${{ inputs.commit }}", "DISPATCH_ID": "${{ inputs.dispatch_id }}"},
        "run": PRECHECK_RECORD_RUN,
    },
    {"name": "Upload pre-check record", "uses": UPLOAD_USES, "with": PRECHECK_UPLOAD},
)


def check_runtime_probe(job, record_run):
    steps = job.get("steps") or []
    if not isinstance(steps, list) or any(not isinstance(step, dict) for step in steps):
        return ["runtime measurement requires a build steps list"]
    probes = [index for index, step in enumerate(steps) if step.get("run") == PROBE_RUN]
    if len(probes) != 1:
        return ["runtime measurement must run exactly once after the tests"]
    index = probes[0]
    violations = []
    if set(steps[index]) - {"name", "run", "env"}:
        violations.append("runtime measurement cannot be skipped, softened or redirected")
    if steps[index].get("env") != BUN_STEP_ENV:
        violations.append("runtime measurement must use exactly the authenticated Bun from step bun")
    tests = steps[index - 1] if index else {}
    if tests.get("run") != TESTS_RUN:
        violations.append("runtime measurement must immediately follow the tests")
    elif set(tests) - {"name", "run", "env"} or tests.get("env") != TESTS_ENV:
        violations.append("the tests must run with exactly the tests input and the authenticated Bun from step bun")
    records = [position for position, step in enumerate(steps) if step.get("run") == record_run]
    if len(records) != 1 or records[0] < index:
        violations.append("the build manifest must be written once, from the runtime measurement, after it")
    return violations


def check_precheck_separation(job):
    steps = job.get("steps") or []
    violations = []
    uploads = []
    forks = []
    for step in steps:
        uses = str(step.get("uses", ""))
        run = str(step.get("run", ""))
        if uses.startswith(("actions/attest", "actions/download-artifact")):
            violations.append(f"precheck job must not use {uses}")
        if uses.startswith("actions/upload-artifact@"):
            uploads.append(step)
        if uses.startswith("actions/checkout@") and (step.get("with") or {}).get("repository") == "EYYCHEEV/oh-my-pi":
            forks.append(step)
        if any(marker in run for marker in RELEASE_ONLY_RUNS):
            violations.append(f"precheck job must not package or sign a binary: {run.splitlines()[0] if run else run!r}")
    if len(uploads) != 1 or set(uploads[0]) - {"name", "uses", "with"} or uploads[0].get("with") != PRECHECK_UPLOAD:
        violations.append("precheck job must upload only precheck.json as mac-precheck")
    if len(forks) != 1 or (forks[0].get("with") or {}).get("ref") != "${{ inputs.commit }}":
        violations.append("precheck job must check out the fork once, at the full input commit")
    return violations


def check_precheck_steps(job):
    steps = job.get("steps")
    if not isinstance(steps, list):
        return ["precheck job steps must be a list"]
    violations = []
    if job.get("env") != PRECHECK_JOB_ENV:
        violations.append(f"precheck job env must be exactly {PRECHECK_JOB_ENV}")
    for index, (step, expected) in enumerate(zip(steps, PRECHECK_STEPS)):
        if step != expected:
            violations.append(f"precheck step {index} is not the allowlisted step {expected['name']!r}")
            break
    if len(steps) != len(PRECHECK_STEPS):
        violations.append(f"precheck job has {len(steps)} steps, the allowlisted sequence has {len(PRECHECK_STEPS)}")
    return violations


def check_job_mode(name, job):
    expected = "${{ inputs.mode == '" + JOB_MODES[name] + "' }}"
    if job.get("if") != expected:
        return [f"job {name} must run only when {expected}, got {job.get('if')!r}"]
    return []


def check_job(name, job):
    if name not in JOB_PERMISSIONS:
        return [f"unexpected job {name}"]
    violations = check_runs_on(name, job) + check_job_permissions(name, job) + check_job_mode(name, job)
    if name == "build":
        violations.extend(check_bun_authentication(job))
        violations.extend(check_runtime_probe(job, MANIFEST_RUN))
    if name == "precheck":
        violations.extend(check_bun_authentication(job))
        violations.extend(check_runtime_probe(job, PRECHECK_RECORD_RUN))
        violations.extend(check_precheck_separation(job))
        violations.extend(check_precheck_steps(job))
    timeout = job.get("timeout-minutes")
    if type(timeout) is not int or not 1 <= timeout <= 359:
        violations.append(f"job {name} timeout-minutes {timeout!r} is not an integer from 1 to 359")
    for index, step in enumerate(job.get("steps") or []):
        run = step.get("run")
        if isinstance(run, str) and "${{" in run:
            violations.append(f"job {name} step {index} has an expression inside run:")
        uses = step.get("uses")
        if isinstance(uses, str) and uses.startswith("actions/checkout@"):
            if (step.get("with") or {}).get("persist-credentials") is not False:
                violations.append(f"job {name} step {index} checkout does not set persist-credentials: false")
    return violations


def check_document(document, text):
    if not isinstance(document, dict):
        return ["workflow is not a mapping"]
    violations = []
    top_level = {"on" if key is True else key for key in document}
    if top_level != WORKFLOW_KEYS:
        violations.append(f"workflow keys are {sorted(map(str, top_level))}, expected exactly {sorted(WORKFLOW_KEYS)}")
    if document.get("name") != "mac-build":
        violations.append(f"workflow name is {document.get('name')!r}, expected 'mac-build'")
    if document.get("defaults") != WORKFLOW_DEFAULTS:
        violations.append(f"workflow defaults are {document.get('defaults')!r}, expected exactly {WORKFLOW_DEFAULTS}")
    if trigger_names(document) != {"workflow_dispatch"}:
        violations.append(f"triggers are {trigger_names(document)!r}, expected only workflow_dispatch")
    if "permissions" not in document or document["permissions"] != {}:
        violations.append(f"top-level permissions are {document.get('permissions')!r}, expected an empty mapping")
    if document.get("run-name") != RUN_NAME:
        violations.append("run-name must carry the precheck commit, tests digest and dispatch id exactly")
    triggers = document.get("on", document.get(True))
    dispatch = triggers.get("workflow_dispatch") if isinstance(triggers, dict) else None
    if not isinstance(dispatch, dict) or dispatch.get("inputs") != INPUTS or set(dispatch) != {"inputs"}:
        violations.append("workflow_dispatch inputs must be exactly the allowlisted inputs with mode defaulting to release")
    uses = []
    walk_uses(document, uses)
    for value in uses:
        if not isinstance(value, str) or FULL_SHA_USES.fullmatch(value) is None:
            violations.append(f"uses {value!r} is not owner/repo@<40 hex commit>")
    jobs = document.get("jobs")
    if not isinstance(jobs, dict) or set(jobs) != set(JOB_PERMISSIONS):
        violations.append(f"jobs are {sorted(jobs) if isinstance(jobs, dict) else jobs!r}, expected build, attest and precheck")
    else:
        for name, job in jobs.items():
            if isinstance(job, dict) and set(job) != JOB_KEYS[name]:
                violations.append(f"job {name} keys are {sorted(map(str, job))}, expected exactly {sorted(JOB_KEYS[name])}")
            violations.extend(check_job(name, job))
    if SECRET_REFERENCE.search(text):
        violations.append("workflow refers to secrets")
    for number, line in enumerate(text.splitlines(), 1):
        if COMMENT.search(line):
            violations.append(f"line {number} holds a comment")
    return violations


def check(path):
    text = Path(path).read_text(encoding="utf-8")
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as error:
        return [f"workflow is not valid YAML: {error}"]
    return check_document(document, text)


def main(argv=None):
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 1:
        print("usage: check_workflow.py WORKFLOW.yml", file=sys.stderr)
        return 2
    violations = check(arguments[0])
    for violation in violations:
        print(f"FAIL: {violation}")
    if violations:
        print(f"{len(violations)} violation(s) in {arguments[0]}")
        return 1
    print(f"OK: {arguments[0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
