# Release Checklist

## Before Release

- [ ] Confirm the canonical version in `app/version.py`.
- [ ] Finalize `CHANGELOG.md` and `docs/releases/<version>.md` with the release date.
- [ ] Run `python -m app.main --version` and `python -m app.main --help`.
- [ ] Run the offline demo against a fresh temporary database.
- [ ] Run `pytest` on supported Python versions, or confirm the CI matrix.
- [ ] Build the Docker image and verify `/ready` without external credentials.
- [ ] Review `.env.example`, configuration docs, migration notes, and limitations.
- [ ] Scan tracked files for secrets and generated artifacts.
- [ ] Confirm screenshots contain current fixture data and no private information.
- [ ] Check README, CLI, API, architecture, deployment, security, and contribution links.

## Release

- [ ] Commit the release preparation changes.
- [ ] Create an annotated `v<version>` tag from the reviewed commit.
- [ ] Push the branch and tag only after CI passes.
- [ ] Publish release notes from `docs/releases/<version>.md`.
- [ ] Verify the release artifact/image reports the expected version.

## After Release

- [ ] Run the documented upgrade against a database backup.
- [ ] Verify dashboard, health, ingestion, reports, and exports in the target environment.
- [ ] Record any known deployment-specific issue without exposing credentials.
- [ ] Start the next changelog section as `Unreleased` when development resumes.
