# Pinned actions

Every `uses:` in `.github/workflows/mac-build.yml` is a full 40-character commit SHA. Verified read-only on Fri Nov 13, 2026 with `gh api` against each official repository. Nothing was downloaded or run.

| Action | Release tag | Pinned commit | Tag published | Commit date | Used by |
|---|---|---|---|---|---|
| `actions/checkout` | `v7.0.1` | `3d3c42e5aac5ba805825da76410c181273ba90b1` | 2026-07-20 | 2026-07-17 | build (builder and fork), attest (builder) |
| `actions/upload-artifact` | `v7.0.2` | `cf430e030ddbb5b0abf93d22962f4752f3646cd9` | 2026-10-07 | 2026-10-07 | build |
| `actions/download-artifact` | `v8.0.2` | `9000827ccba6bdab643e8b6fd33ac0654aef8333` | 2026-10-07 | 2026-10-07 | attest |
| `actions/attest` | `v4.2.2` | `1e69f48acb82d1966a394da916b4c1698aa569d6` | 2026-08-04 | 2026-08-04 | attest |

## How each pin was verified

For each row, with `OWNER/REPO` and `TAG` from the table:

1. Latest release: `gh api repos/OWNER/REPO/releases/latest --jq '.tag_name + " " + .published_at'` returned the tag above.
2. Tag to commit: `gh api repos/OWNER/REPO/git/ref/tags/TAG --jq '.object.type + " " + .object.sha'` returned `commit` and the pinned SHA for all four. Every tag is a lightweight tag, so there was no annotated tag object to dereference.
3. Commit exists in the official repository and is signed: `gh api repos/OWNER/REPO/commits/SHA --jq '.sha + " " + .commit.committer.date + " verified=" + (.commit.verification.verified|tostring)'` returned the same SHA and `verified=true` for all four.

The checkout, upload-artifact and download-artifact pins are newer than upstream's (v6.0.3, v7.0.1 and v8.0.1) because they are the latest releases at the recorded date.

## Inputs read at each pin

- `actions/attest@1e69f48a...`: `action.yml` and `README.md` read at the pinned commit on Nov 13, 2026. With no `sbom-path` and no predicate inputs it creates SLSA build provenance. Documented permissions for that mode are `id-token: write`, `contents: read`, `attestations: write` (README "Provenance Attestation (Default)" example; GitHub Docs, "Using artifact attestations to establish provenance for builds", read Nov 13, 2026). `artifact-metadata: write` is documented only for storage records, which `src/attest.ts` creates only when `push-to-registry` is true. This workflow never sets `push-to-registry`, so the attest job does not get `artifact-metadata`, `packages` or any other permission.
- `actions/upload-artifact@cf430e03...`: inputs `name`, `path`, `if-no-files-found`, `retention-days` exist in `action.yml` at the pin; runs on `node24`.
- `actions/download-artifact@9000827c...`: inputs `name`, `path` exist; with no `github-token` it reads only this run's artifacts; `digest-mismatch` defaults to `error`.
- `actions/checkout@3d3c42e5...`: inputs `repository`, `ref`, `path`, `persist-credentials` exist; the workflow sets `persist-credentials: false` on every checkout.

## Bun release trust pin and runner tools

Bun's full release-signing fingerprint is `F3DCC08A8572C0749B3E18888EAB4D40A7B22B59`. The public key is committed in `scripts/bun-release-key.asc`. `scripts/setup_bun.py` checks that fingerprint before import into its own 0700 keyring, requires a good signature from the same signer on `SHASUMS256.txt.asc`, and checks the exact archive digest from the verified body before extracting or executing Bun.

The fingerprint was confirmed on 2026-11-13 in Bun's official [release script](https://github.com/oven-sh/bun/blob/d0f4fdd65fbd9548042aa5cd811a08f837ce4b4c/packages/bun-release/scripts/upload-assets.ts) and [Debian Dockerfile](https://github.com/oven-sh/bun/blob/d0f4fdd65fbd9548042aa5cd811a08f837ce4b4c/dockerhub/debian/Dockerfile), both at commit `d0f4fdd65fbd9548042aa5cd811a08f837ce4b4c`, committed 2026-11-13T07:54:03Z. The committed public key bytes were verified with that fingerprint in the devbox Bun 1.4.4 signature check.

Bun's exact version comes from the already verified fork commit's `package.json`. The bootstrap downloads only `bun-darwin-aarch64.zip` and `SHASUMS256.txt.asc` for that exact `bun-vX.Y.Z` release. It publishes the private binary directory on PATH only after verification and a secondary exact-version check. These assets are delivery channels from one publisher; the reviewed fingerprint is the trust root. A key change requires its own review.

The [macOS 15 arm64 image inventory](https://github.com/actions/runner-images/blob/62b20e715386d6a54c8b9cad40e10a36039db4f2/images/macos/macos-15-arm64-Readme.md), read 2026-11-13, lists GnuPG 2.5.24 and Rustup 1.29.3 on image 20261104.0514.1. Neither needs an install action. Rust uses the runner image's rustup and the exact source channel pin.

## Updating a pin

Repeat the three commands for the new tag, edit the SHA in `.github/workflows/mac-build.yml` and in the Actions allow list (see `SETTINGS.md`), run `python3 checks/check_workflow.py .github/workflows/mac-build.yml` and the tests, and record the new row here.
