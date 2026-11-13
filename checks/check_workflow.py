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
}


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
    if name == "build":
        return [] if runs_on == "macos-15" else [f"job build runs-on is {runs_on!r}, expected 'macos-15'"]
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
    if set(authentication) - {"name", "run", "env"}:
        violations.append("Bun authentication cannot be skipped, softened or redirected")
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


def check_job(name, job):
    if name not in JOB_PERMISSIONS:
        return [f"unexpected job {name}"]
    violations = check_runs_on(name, job) + check_job_permissions(name, job)
    if name == "build":
        violations.extend(check_bun_authentication(job))
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
    if trigger_names(document) != {"workflow_dispatch"}:
        violations.append(f"triggers are {trigger_names(document)!r}, expected only workflow_dispatch")
    if "permissions" not in document or document["permissions"] != {}:
        violations.append(f"top-level permissions are {document.get('permissions')!r}, expected an empty mapping")
    uses = []
    walk_uses(document, uses)
    for value in uses:
        if not isinstance(value, str) or FULL_SHA_USES.fullmatch(value) is None:
            violations.append(f"uses {value!r} is not owner/repo@<40 hex commit>")
    jobs = document.get("jobs")
    if not isinstance(jobs, dict) or set(jobs) != set(JOB_PERMISSIONS):
        violations.append(f"jobs are {sorted(jobs) if isinstance(jobs, dict) else jobs!r}, expected build and attest")
    else:
        for name, job in jobs.items():
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
