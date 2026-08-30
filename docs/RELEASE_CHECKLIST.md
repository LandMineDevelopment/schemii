# Release Checklist

## Prepare

- Set the intended stable semantic version once in `VERSION`; confirm `pyproject.toml` still reads dynamic setuptools metadata from that file and `CHANGELOG.md` has the matching heading.
- Review persisted dashboard, schema, metadata, and launcher compatibility. Back up and test representative upgrades from every supported stored format and metadata version.
- Confirm every pinned container foundation still resolves for Linux/AMD64 and review dependency, action, and base-image updates.
- Run the complete repository verification in `agent_guide.md`. CI must pass on the reviewed default-branch commit.
- Confirm the CI `Immutable artifacts` job built exactly one wheel/sdist and one application, metadata, and OpenCode image set; all four Compose smokes consumed those images with `--no-build`.
- Confirm the exact wheel and sdist installed independently in clean virtual environments with both entry points, all web assets, all metadata migrations, and the exact 40-character build revision.
- Confirm final artifact inspection examined the generated source archive, nested wheel/sdist, and every saved filesystem layer from all three image archives. The source must contain exactly the synthetic `examples/postgres/001_bookstore.sql` database seed and no profile, schema, dashboard, credential, PostgreSQL cluster/dump, backup, or runtime state.

## Select Candidate

- Record the successful default-branch CI run ID, its full 40-character `head_sha`, and the matching `VERSION`.
- Confirm its `release-candidate-<sha>` artifact contains version-and-SHA-addressed source, Python-package, application-image, metadata-image, and OpenCode-image archives, plus `release-manifest.json` and `SHA256SUMS`.
- Verify the manifest binds every archive hash, all three exact Docker image IDs, version, revision, and `linux/amd64` platform.
- Configure a protected GitHub `production-release` environment with required reviewers. This repository cannot create or alter that external policy.

## Promote

- Manually dispatch **Promote release** with the candidate CI run ID, exact `VERSION`, and confirmation `PROMOTE`.
- Approve the protected environment only after reviewing the selected run and candidate identity. Do not create or push a release tag separately.
- Confirm promotion accepts only a successful `push` run from this repository's default branch and `.github/workflows/ci.yml`.
- Confirm promotion downloads and verifies the existing candidate, checks every GitHub artifact attestation, reloads and re-inspects all exact image IDs, and performs no build or smoke-test replay.
- Confirm exact version and revision tags are published for the application, metadata, and OpenCode GHCR images. No mutable `latest` tag is permitted.
- Confirm OCI provenance exists for all three published registry digests and file provenance exists for every release asset and `published-images.json`.
- Confirm the workflow creates immutable tag `v<VERSION>` at the candidate revision and publishes the candidate files without replacing an existing release or tag.

## Verify Publication

- Download all assets to a clean directory, run `sha256sum -c SHA256SUMS` (or `shasum -a 256 -c SHA256SUMS`), and verify each GitHub attestation.
- Compare `published-images.json` with the GHCR version tags and verify their OCI attestations.
- Load the three architecture-qualified image archives on a Linux/AMD64 Docker engine, select their version-and-revision tags, and start through the launcher. The launcher must inspect every selected image and invoke Compose with `--no-build`.
- Fetch `/`, `/api/session`, and `/api/readiness` from both products. Confirm readiness reports the exact promoted `{version, revision}` and expected generic loopback/public-origin access mode.
- Record any registry retention, environment approval, immutable-release setting, or tag/package protection that remains an external organizational control.

CI builds and tests candidates; it never publishes them. Promotion publishes only a human-selected, successful default-branch candidate and never rebuilds it.
