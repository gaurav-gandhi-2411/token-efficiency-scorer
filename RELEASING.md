# Releasing tracegauge

Every release from `0.1.0` through `0.10.0` was published by hand — a manual `uv build` +
upload with a locally-held PyPI API token, nothing about the process written down. Starting
with `0.10.1`, releases are tag-triggered and published by CI over PyPI Trusted Publishing
(OIDC) — no token generated, handled, or stored anywhere, by anyone, ever again.

This document describes the actual flow, written after running it for real for `0.10.1`
(2026-08-13) — not a plan for how it should work.

## The flow

1. **Bump the version** in exactly two places (`grep -rn "^version" pyproject.toml` plus a
   search for any hardcoded version assertions in `tests/` first — see the gotcha below,
   there was a third place on this exact release):
   - `pyproject.toml`: `[project].version`
   - `tests/test_packaging.py`: `test_package_name_is_tracegauge` asserts
     `meta["Version"] == "..."` literally — **this is not derived from anything, it's a
     hardcoded string that silently goes stale if you forget it.** It will fail loudly in CI
     if you do, which is the whole reason it's worth calling out here rather than just
     fixing it quietly each time.
   - Add a `CHANGELOG.md` entry documenting what shipped and why, following the existing
     format (a note at the top of the file tracks which versions were actually published to
     PyPI vs. built-but-held).
2. **Commit and open a PR.** CI (`ci.yml`) runs the normal lint/test suite against the
   version-bumped code. Merge once green.
3. **Tag the merged commit and push the tag:**
   ```bash
   git checkout master && git pull
   git tag vX.Y.Z
   git push origin vX.Y.Z
   ```
4. **`release.yml` takes it from there** — triggered by the `v*` tag push, it checks out
   that exact tag (never a branch head), builds with `uv build`, sanity-checks the artifact
   with `twine check`, and publishes via `pypa/gh-action-pypi-publish@release/v1` using the
   `pypi` GitHub Environment's OIDC token. Confirm it actually succeeded — don't just trust
   a green checkmark, read the log for the real upload confirmation:
   ```bash
   gh run list --repo gaurav-gandhi-2411/token-efficiency-scorer --workflow release.yml --limit 1
   gh run view <run-id> --repo gaurav-gandhi-2411/token-efficiency-scorer --log | grep -A1 "View at"
   ```
   A genuine publish ends with a Sigstore `Successfully verified SCT...` line and
   `View at: https://pypi.org/project/tracegauge/X.Y.Z/` — that URL is the actual proof, not
   the workflow's green checkmark alone (a green run that never reached the upload step
   would look identical in the checks UI).
5. **Post-publish verify from a fresh environment against the real index** — see below.
6. **Do not yank or delete a published version**, ever, including a superseded one. Deletion
   burns the version number permanently and breaks every pinned install of it. If a release
   has a real problem, ship a new version that supersedes it — that's what versioning is for.

## The tag-must-match-pyproject gotcha

`pyproject.toml` has no dynamic versioning (no `setuptools-scm`, no `hatch-vcs`) — the
published version comes entirely from `[project].version`, not from the git tag name.
**The tag is a trigger, not a version source.** If you push `vX.Y.Z` while
`pyproject.toml` still says the previous version, `release.yml` builds and publishes the
*previous* version under the *new* tag — a real, permanent mismatch, since step 6 above
means it can't be un-published. Always confirm the version landed on `master` and merged
*before* tagging:

```bash
git show origin/master:pyproject.toml | grep "^version"
```

This gotcha bit the `adk-tracegauge` sibling package's `0.1.0rc1` release before it ever got
tagged — caught in review before the tag was pushed, not after. Same repo pattern, same
fix: bump-then-merge-then-tag, in that order, every time.

## Post-publish verification

Manual, deliberately — not automated (see the backlog note in this repo's memory/planning
docs: adding an automated post-publish step to `release.yml` is planned as one dedicated
change across all PyPI-published repos in this portfolio, not built yet).

```bash
# Fresh venv at a SHORT path -- see the MAX_PATH trap below for why "short" is load-bearing
uv venv --python 3.11 C:\tg-verify
uv pip install --no-cache tracegauge==X.Y.Z --index-url https://pypi.org/simple/ --python C:\tg-verify\Scripts\python.exe

C:\tg-verify\Scripts\tes.exe --version   # confirm it reports X.Y.Z

# Confirm both license files actually shipped in the distribution's own metadata --
# not just the SPDX header comments in source, the dist-info itself
find C:\tg-verify\Lib\site-packages\tracegauge-X.Y.Z.dist-info -iname "*LICENSE*"
# expect: .../licenses/LICENSE and .../licenses/LICENSE-APACHE

rm -rf C:\tg-verify
```

## The MAX_PATH trap (Windows) — presents as a package defect, isn't one

Verifying `adk-tracegauge` (a downstream package that depends on `tracegauge` plus
`google-adk[eval]`, whose transitive dependency tree includes `google-cloud-aiplatform` —
notoriously deep, long file paths) from a venv created under a deeply-nested temp/scratch
directory produced a real, reproducible `ModuleNotFoundError` on import — a file that
genuinely existed on disk, at a path 264 characters long, one past Windows' classic
260-character `MAX_PATH` limit. The traceback looks exactly like a broken/corrupted install
or a real packaging bug in the dependency — it is neither. Re-running the identical install
and import from a short path (`C:\tg-rc1-test` rather than a `...\AppData\Local\Temp\...`
scratch path several directories deep) passed cleanly on the same machine, same Python,
same package versions.

**Lesson:** always create post-publish verification venvs at a short path
(`C:\<name>`, not a deeply nested temp directory) on Windows, especially for any package
whose dependency tree touches `google-cloud-aiplatform` or similar deep-namespace packages.
If a fresh-venv verification fails with a `ModuleNotFoundError` for a module that
demonstrably exists on disk, check the full resolved path length before concluding the
package itself is broken.
