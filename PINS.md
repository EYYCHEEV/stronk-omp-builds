# Pinned actions

Every `uses:` in `.github/workflows/mac-build.yml` is a full 40-character commit SHA. Verified read-only on Fri Nov 13, 2026 with `gh api` against each official repository. Nothing was downloaded or run.

| Action | Release tag | Pinned commit | Tag published | Commit date | Used by |
|---|---|---|---|---|---|
| `actions/checkout` | `v7.0.1` | `3d3c42e5aac5ba805825da76410c181273ba90b1` | 2026-07-20 | 2026-07-17 | build (builder and fork), attest (builder) |
| `oven-sh/setup-bun` | `v2.2.0` | `0c5077e51419868618aeaa5fe8019c62421857d6` | 2026-03-14 | 2026-03-14 | build |
| `actions/upload-artifact` | `v7.0.2` | `cf430e030ddbb5b0abf93d22962f4752f3646cd9` | 2026-10-07 | 2026-10-07 | build |
| `actions/download-artifact` | `v8.0.2` | `9000827ccba6bdab643e8b6fd33ac0654aef8333` | 2026-10-07 | 2026-10-07 | attest |
| `actions/attest` | `v4.2.2` | `1e69f48acb82d1966a394da916b4c1698aa569d6` | 2026-08-04 | 2026-08-04 | attest |

## How each pin was verified

For each row, with `OWNER/REPO` and `TAG` from the table:

1. Latest release: `gh api repos/OWNER/REPO/releases/latest --jq '.tag_name + " " + .published_at'` returned the tag above.
2. Tag to commit: `gh api repos/OWNER/REPO/git/ref/tags/TAG --jq '.object.type + " " + .object.sha'` returned `commit` and the pinned SHA for all five. Every tag is a lightweight tag, so there was no annotated tag object to dereference.
3. Commit exists in the official repository and is signed: `gh api repos/OWNER/REPO/commits/SHA --jq '.sha + " " + .commit.committer.date + " verified=" + (.commit.verification.verified|tostring)'` returned the same SHA and `verified=true` for all five.

The `oven-sh/setup-bun` pin equals the one upstream's own workflow uses (`0c5077e5...`, v2.2.0). The three `actions/*` pins are newer than upstream's (`checkout` v6.0.3, `upload-artifact` v7.0.1, `download-artifact` v8.0.1) because they are the latest releases today.

## Inputs read at each pin

- `actions/attest@1e69f48a...`: `action.yml` and `README.md` read at the pinned commit on Nov 13, 2026. With no `sbom-path` and no predicate inputs it creates SLSA build provenance. Documented permissions for that mode are `id-token: write`, `contents: read`, `attestations: write` (README "Provenance Attestation (Default)" example; GitHub Docs, "Using artifact attestations to establish provenance for builds", read Nov 13, 2026). `artifact-metadata: write` is documented only for storage records, which `src/attest.ts` creates only when `push-to-registry` is true. This workflow never sets `push-to-registry`, so the attest job does not get `artifact-metadata`, `packages` or any other permission.
- `actions/upload-artifact@cf430e03...`: inputs `name`, `path`, `if-no-files-found`, `retention-days` exist in `action.yml` at the pin; runs on `node24`.
- `actions/download-artifact@9000827c...`: inputs `name`, `path` exist; with no `github-token` it reads only this run's artifacts; `digest-mismatch` defaults to `error`.
- `oven-sh/setup-bun@0c5077e5...`: inputs `bun-version` and `no-cache` exist; `no-cache: true` keeps the job from reading or saving a cached Bun.
- `actions/checkout@3d3c42e5...`: inputs `repository`, `ref`, `path`, `persist-credentials` exist; the workflow sets `persist-credentials: false` on every checkout.

## Not pinned

Rust is installed with the runner image's own `rustup` (Rustup 1.29.3 on image 20261104.0514.1, per the macos-15-arm64 image readme read Nov 13, 2026), not with an action. Bun itself is downloaded by `oven-sh/setup-bun` at the exact version the fork's `package.json` names.

## Updating a pin

Repeat the three commands for the new tag, edit the SHA in `.github/workflows/mac-build.yml` and in the Actions allow list (see `SETTINGS.md`), run `python3 checks/check_workflow.py .github/workflows/mac-build.yml` and the tests, and record the new row here.
