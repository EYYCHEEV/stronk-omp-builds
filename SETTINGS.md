# Repository settings and design notes

Status: written locally only. Nothing here has been run against GitHub. Every `gh api` command below changes or reads `EYYCHEEV/stronk-omp-builds`. The Captain said yes to that repository (FC-6738); each command still runs only on Ives's OK for its exact line.

API facts were read from GitHub Docs on Fri Nov 13, 2026: "REST API endpoints for GitHub Actions permissions", "REST API endpoints for protected branches", "REST API endpoints for repositories", "Workflow syntax for GitHub Actions", "Secure use reference", "Using artifact attestations to establish provenance for builds", and the GitHub CLI manual page `gh attestation verify`.

## Order of use

1. Create the empty public repository (step 0).
2. Push `main` from this folder.
3. Apply settings 1 to 7.
4. Run every read-only check; every one must print `true`.
5. Only then dispatch the workflow.

## Settings and the commands that would set them (not run)

Step 0, create the repository empty, public, with no issues, wiki, projects or pull requests:

```
gh api -X POST user/repos -f name=stronk-omp-builds -f description='Stronk omp build jobs: builds and tests the macOS omp binary for tagged EYYCHEEV/oh-my-pi commits' -F private=false -F has_issues=false -F has_wiki=false -F has_projects=false -F auto_init=false
gh api -X PATCH repos/EYYCHEEV/stronk-omp-builds -F has_pull_requests=false
```

Setting 1, Actions on, only the allowed list, SHA pinning required:

```
gh api -X PUT repos/EYYCHEEV/stronk-omp-builds/actions/permissions -F enabled=true -f allowed_actions=selected -F sha_pinning_required=true
```

Setting 2, the allowed list is exactly the five pinned actions, nothing GitHub-owned or Marketplace-verified by default (the list must equal the `uses:` lines and `PINS.md`; `patterns_allowed` applies to public repositories, per the same docs page):

```
gh api -X PUT repos/EYYCHEEV/stronk-omp-builds/actions/permissions/selected-actions --input - <<'JSON'
{
  "github_owned_allowed": false,
  "verified_allowed": false,
  "patterns_allowed": [
    "actions/attest@1e69f48acb82d1966a394da916b4c1698aa569d6",
    "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
    "actions/download-artifact@9000827ccba6bdab643e8b6fd33ac0654aef8333",
    "actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9",
    "oven-sh/setup-bun@0c5077e51419868618aeaa5fe8019c62421857d6"
  ]
}
JSON
```

Setting 3, the default workflow token is read-only and Actions cannot approve pull requests:

```
gh api -X PUT repos/EYYCHEEV/stronk-omp-builds/actions/permissions/workflow -f default_workflow_permissions=read -F can_approve_pull_request_reviews=false
```

Setting 4, workflows from outside contributors always need a maintainer's approval (a backstop; see "Forks and outside pull requests" below):

```
gh api -X PUT repos/EYYCHEEV/stronk-omp-builds/actions/permissions/fork-pr-contributor-approval -f approval_policy=all_external_contributors
```

Setting 5, `main` cannot be force-pushed or deleted, including by the owner. Ordinary fast-forward pushes by the owner still work because no status checks, pull request reviews or push restrictions are required:

```
gh api -X PUT repos/EYYCHEEV/stronk-omp-builds/branches/main/protection --input - <<'JSON'
{
  "required_status_checks": null,
  "enforce_admins": true,
  "required_pull_request_reviews": null,
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false
}
JSON
```

Setting 6, no Actions secrets, variables, environments, deploy keys, webhooks or self-hosted runners: create none. There is no command for this; the checks below prove it stays true.

Setting 7, the workflow file on `main` is the reviewed one: push only the committed file from this folder, then run the last check below.

## Read-only checks that prove them (not run)

Each command prints `true` when the setting holds.

```
gh api repos/EYYCHEEV/stronk-omp-builds --jq '[.private, .has_issues, .has_wiki, .has_projects, .has_pull_requests, .default_branch] == [false,false,false,false,false,"main"]'
gh api repos/EYYCHEEV/stronk-omp-builds/actions/permissions --jq '[.enabled, .allowed_actions, .sha_pinning_required] == [true,"selected",true]'
gh api repos/EYYCHEEV/stronk-omp-builds/actions/permissions/selected-actions --jq '[.github_owned_allowed, .verified_allowed, (.patterns_allowed | sort)] == [false,false,["actions/attest@1e69f48acb82d1966a394da916b4c1698aa569d6","actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1","actions/download-artifact@9000827ccba6bdab643e8b6fd33ac0654aef8333","actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9","oven-sh/setup-bun@0c5077e51419868618aeaa5fe8019c62421857d6"]]'
gh api repos/EYYCHEEV/stronk-omp-builds/actions/permissions/workflow --jq '[.default_workflow_permissions, .can_approve_pull_request_reviews] == ["read", false]'
gh api repos/EYYCHEEV/stronk-omp-builds/actions/permissions/fork-pr-contributor-approval --jq '.approval_policy == "all_external_contributors"'
gh api repos/EYYCHEEV/stronk-omp-builds/branches/main/protection --jq '[.allow_force_pushes.enabled, .allow_deletions.enabled, .enforce_admins.enabled] == [false,false,true]'
gh api repos/EYYCHEEV/stronk-omp-builds/actions/secrets --jq '.total_count == 0'
gh api repos/EYYCHEEV/stronk-omp-builds/dependabot/secrets --jq '.total_count == 0'
gh api repos/EYYCHEEV/stronk-omp-builds/actions/variables --jq '.total_count == 0'
gh api repos/EYYCHEEV/stronk-omp-builds/environments --jq '.total_count == 0'
gh api repos/EYYCHEEV/stronk-omp-builds/actions/runners --jq '.total_count == 0'
gh api repos/EYYCHEEV/stronk-omp-builds/keys --jq 'length == 0'
gh api repos/EYYCHEEV/stronk-omp-builds/hooks --jq 'length == 0'
gh api repos/EYYCHEEV/stronk-omp-builds/actions/workflows --jq '[.workflows[].path] == [".github/workflows/mac-build.yml"]'
```

To prove the workflow file on `main` is the committed one, the blob SHA GitHub reports must equal the local one:

```
gh api 'repos/EYYCHEEV/stronk-omp-builds/contents/.github/workflows/mac-build.yml?ref=main' --jq .sha
git hash-object .github/workflows/mac-build.yml
```

## Forks and outside pull requests

Three layers, none of them depending on another:

1. The workflow has one trigger, `workflow_dispatch`. A pull request, fork or push cannot start it. `checks/check_workflow.py` fails on any other trigger.
2. `has_pull_requests=false` removes pull requests from the repository (field documented under "Update a repository", Nov 13, 2026).
3. The fork approval policy `all_external_contributors` makes GitHub hold any workflow run an outside contributor's pull request would start until a maintainer approves.

Public repositories cannot switch forking off (`allow_forking` applies to private repositories only). A fork carries a copy of the workflow, but a run in someone else's fork signs attestations under that fork's name, and the updater verifies `--repo EYYCHEEV/stronk-omp-builds`, so such a run cannot produce an accepted binary. `scripts/validate_inputs.py` also refuses any run whose repository is not `EYYCHEEV/stronk-omp-builds`.

## What the attestation proves, and how the updater checks it

The attest job signs two files with `actions/attest` (SLSA build provenance, Sigstore public-good instance, no stored key): `omp-darwin-arm64` and `build-manifest.json`. The signature proves the files were produced by this workflow file in this repository on `refs/heads/main`. It does not by itself name the fork commit, because the workflow inputs are not part of the provenance. The binding to the fork commit is the signed `build-manifest.json`, whose `fork.tag`, `fork.commit` and `binary.sha256` the updater must compare with what it pushed and downloaded.

Verification the updater runs (not run here; needs `gh` 2.102.0 or newer, because earlier versions matched `--source-ref` without case, advisory GHSA-4mq3-hpgx-9cx8):

```
gh attestation verify omp-darwin-arm64 --repo EYYCHEEV/stronk-omp-builds --signer-workflow EYYCHEEV/stronk-omp-builds/.github/workflows/mac-build.yml --source-ref refs/heads/main --deny-self-hosted-runners
gh attestation verify build-manifest.json --repo EYYCHEEV/stronk-omp-builds --signer-workflow EYYCHEEV/stronk-omp-builds/.github/workflows/mac-build.yml --source-ref refs/heads/main --deny-self-hosted-runners
```

Then it must read `build-manifest.json` and require `fork.repository == EYYCHEEV/oh-my-pi`, the exact tag and commit it pushed, `binary.name == omp-darwin-arm64`, `binary.sha256` equal to the sha256 of the binary it downloaded, `omp_version` equal to the version it expects, and every `tests[].exit_code == 0`.

The `gh attestation verify` manual (read Nov 13, 2026) warns that only the certificate fields and timestamps in the result cannot be forged by the workflow; predicate content can. That is why the workflow keeps all fork-specific claims in the manifest and the updater checks the manifest against its own records instead of trusting any text in the attestation.

## Workflow design notes

These replace code comments (rule 29).

- Trigger and ref: only `workflow_dispatch`. `scripts/validate_inputs.py` refuses unless the event is `workflow_dispatch`, the ref is `refs/heads/main` and the repository is `EYYCHEEV/stronk-omp-builds`. A dispatch may name any branch and GitHub then runs that branch's copy of the workflow file, so the ref check stops an accidental dispatch from a branch. It is not the enforcement against a hostile branch, because that branch's scripts would run the check; the enforcement is `--source-ref refs/heads/main` in the updater's attestation verification, which rejects anything signed from another ref.
- Inputs: `tag` must fully match `stronk-omp-[0-9]{8}T[0-9]{6}Z-[0-9]+`, `commit` must be 40 lowercase hex characters, `tests` must parse as described below. Inputs reach scripts only through `env:`. No `${{ }}` expression appears inside any `run:` text (GitHub's "Secure use reference", script injection, Nov 13, 2026). `checks/check_workflow.py` fails on one.
- Checkout: builder repository and fork are checked out separately with `persist-credentials: false`. `scripts/verify_checkout.py` refuses unless `HEAD` and `refs/tags/<tag>^{commit}` both equal `commit`. The tag is fetched by exact ref, so a branch cannot stand in for it.
- Toolchain pins: `scripts/source_pins.py` reads the fork's own files. `packageManager` of `bun@X.Y.Z` or `bun@>=X.Y.Z` means exactly `X.Y.Z`; any other spelling is refused. The Rust channel must be `nightly-YYYY-MM-DD` or `X.Y.Z`; `stable`, `beta`, `nightly` and `1.90` are refused. The workflow then checks the installed Bun version and the active Rust toolchain against the pins.
- Rust: installed with the runner image's `rustup` using the `minimal` profile, and selected for later steps with `RUSTUP_TOOLCHAIN`. The components and extra targets named in `rust-toolchain.toml` (rustfmt, clippy, rust-analyzer, Linux and Windows targets) are not needed for a host build and are not installed. `RUSTUP_AUTO_INSTALL=0` makes a missing toolchain fail instead of downloading during a build.
- Natives: `OMP_NATIVE_BUILD_BACKEND=cargo` keeps `bun run build:native` on the host Cargo path (the fork's `scripts/bazel-natives.ts` defaults to it for `host`), so Bazel is never used. `CARGO_INCREMENTAL=0` keeps incremental build state off the 14 GB runner disk. Neither changes what the source does.
- No caches: `setup-bun` runs with `no-cache: true`; no `actions/cache` step exists, so no earlier run can leave bytes in this build.
- Runner: `macos-15` (Apple M1, 3 cores, 7 GB memory, 14 GB disk, free and unlimited on public repositories per "GitHub-hosted runners reference", Nov 13, 2026), never an `-xlarge` or `-large` label. Build timeout 150 minutes, attest timeout 15, both far under the 360-minute cap. The last build step prints free disk space so the first rehearsal shows how tight 14 GB is.
- Binary checks: `--version` must print exactly `omp/<coding-agent version from the source>`; `--smoke-test` must exit 0, both run with a minimal environment (`PATH`, a throwaway `HOME`, `XDG_DATA_HOME`, `TMPDIR`) so no runner variable reaches the binary; `codesign --verify --strict` must pass; `lipo -archs` must say `arm64`. The binary is ad-hoc signed by the fork's own build script, not Developer ID signed.
- Manifest: `build-manifest.json` records fork repository, tag and commit, binary name, sha256 and size, omp version, Bun version, Rust channel, runner `ImageOS` and `ImageVersion`, macOS version, the builder commit and run id, and every test command with its exit code. It is written only when every test exited 0.
- Artifact: one artifact named `mac-build` holding the binary and the manifest, kept 7 days (the action allows 1 to 90). The release on the fork is the permanent copy. A zipped artifact does not keep file modes (`actions/upload-artifact` README at the pinned commit, "Permission Loss", read Nov 13, 2026), so whoever downloads the binary must `chmod 755` it before running; the attested sha256 does not depend on the mode.
- Attest job: Ubuntu, permissions `contents: read`, `id-token: write`, `attestations: write` only. It downloads the artifact, refuses unless it holds exactly those two files, the manifest names the dispatched tag and commit and this run's builder commit and run id, the binary sha256 recomputed here equals the manifest's, and every recorded test passed. Only then does it attest both files.

## Tests input grammar (`scripts/run_tests.py`)

`tests` is a JSON list of 1 to 128 argv lists, at most 49152 characters. The character limit sits under the 65,535-character cap GitHub puts on `workflow_dispatch` inputs ("Workflow syntax for GitHub Actions", `on.workflow_dispatch.inputs`, read Nov 13, 2026), which leaves room for the tag and commit inputs. No shell is ever used: each entry runs as a list through `subprocess` with `shell=False`, in the fork checkout. Every entry is checked before any entry runs; one refused entry runs nothing. An entry has 2 to 64 arguments; each argument is 1 to 256 printable ASCII characters, except `--test-name-pattern=<value>`, which may be up to 276 characters (the 20-character prefix plus a value of 256).

| Form | Allowed | Refused |
|---|---|---|
| `["bun","test", ...]` | one or more existing repository-relative paths made of `A-Za-z0-9_./+@-` (no `..`, no empty parts, no leading `-`, must resolve inside the checkout after symlinks); options `--bail`, `--only-failures`, `--timeout=<digits>`; the test name pattern in two forms, `--test-name-pattern <value>` (the value is the next argument and must not start with `-`) and `--test-name-pattern=<value>` (any value). A value is 1 to 256 printable ASCII characters, spaces and punctuation included, and is never read as a path; the option may come before the paths and may repeat; at least one path is still required | every other option, including `--preload`, `-r`, `--require`, `--import`, `--config`, `-c`, `-t`, `--bunfig`, `--env-file`, `--cwd`, `--eval`, lookalikes such as `--test-name-patterns=x`, and a bare `--`; an empty or missing pattern value, a value longer than 256 characters, a separate value that starts with `-` |
| `["cargo","test", ...]` | `--workspace --lib --bins --tests --doc --release --locked --offline --no-fail-fast --quiet -q`; `-p/--package`, `--test`, `--exclude` with a plain crate or test name; `--manifest-path` to an existing `Cargo.toml` inside the checkout; plain test-name filters; after `--`: `--nocapture --exact --ignored --include-ignored --show-output --quiet -q --test-threads=<digits>` and plain test-name filters | `+toolchain`, `--config`, `-Z`, `--target-dir`, `--target`, `--features`, `--message-format`, `--profile`, `--test-name-pattern` in either form, and any other option or harness argument |
| anything else | nothing | other programs, absolute or relative program paths, other verbs, shell strings, entries that are not lists |

The program is looked up on `PATH` and refused if it sits inside the checkout. A test that outlives `--timeout-seconds` (default 3000) is killed with its process group and recorded as exit code 124.

The fork's contract list, counted at the time of writing, holds 65 `bun test` entries (16 with a name pattern, the longest pattern 80 characters, at most 10 paths per entry) and one `python3` audit command, which this runner refuses because it only runs `bun test` and `cargo test`. Each entry runs one after the other on the 3-core runner, so the whole list has to fit inside the build job's 150-minute timeout, which is the only overall cap. Nothing has measured that yet; the first rehearsal run measures it, and the timeout is then raised or the list split by whoever owns the contract list.

## Local checks

From the repository root, no installs needed (Python 3.11 or newer; PyYAML only for the workflow check):

```
python3 -m unittest discover -s tests
python3 checks/check_workflow.py .github/workflows/mac-build.yml
```

`checks/check_workflow.py` fails when: any `uses:` is not `owner/repo@<40 hex>`; any trigger other than `workflow_dispatch`; top-level `permissions` is not `{}`; a job holds a permission beyond `contents: read` (build) or `contents: read`, `id-token: write`, `attestations: write` (attest); a `run:` block holds `${{`; build `runs-on` is not `macos-15` or attest `runs-on` is not a standard Ubuntu label. It also fails on any job other than `build` and `attest`, a missing or over-long `timeout-minutes`, a checkout without `persist-credentials: false`, a reference to secrets, and any YAML comment.
