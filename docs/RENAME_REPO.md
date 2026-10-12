# Renaming the repository to `tracegauge`

Preparation for renaming the GitHub repository `gaurav-gandhi-2411/token-efficiency-scorer` to `gaurav-gandhi-2411/tracegauge`.
Nothing in this branch renames anything and no URL has been changed yet: the old URLs still resolve,
and a README rewritten before the rename would point its badge and image at a repository name that
does not exist yet. The rename is done by the repository owner at merge time, then one
follow-up commit rewrites the links with `scripts/rename_repo_urls.py`. Keep the gap between the
rename and that follow-up short: same day (see step 6).

## Ordered steps

1. **Merge the pull request** that carries this document and the script. Do not release yet.
2. **Rename the repository.** Settings, "Repository name", type `tracegauge`, Rename. Or:
   `gh repo rename tracegauge --repo gaurav-gandhi-2411/token-efficiency-scorer`.
3. **Update the PyPI Trusted Publisher before the next release** (see the next section). This is the
   step that can break a release; the rename itself breaks nothing else here.
4. **Check the redirect and the settings that should have followed the repository:**
   ```bash
   curl -sI https://github.com/gaurav-gandhi-2411/token-efficiency-scorer | head -3        # expect a 301 to .../tracegauge
   gh api repos/gaurav-gandhi-2411/tracegauge --jq .full_name              # expect gaurav-gandhi-2411/tracegauge
   gh api repos/gaurav-gandhi-2411/tracegauge/branches/master/protection --jq '.required_status_checks.contexts'
   gh api repos/gaurav-gandhi-2411/tracegauge/environments --jq '.environments[].name'   # expect pypi
   ```
5. **Update local clones:** `git remote set-url origin https://github.com/gaurav-gandhi-2411/tracegauge.git`
   (GitHub says old clone, fetch and push URLs keep working, but recommends updating them). The
   remote URL is all that has to change. Renaming the local checkout *directory* is optional and has
   side effects: Claude Code keys its per-project history and memory by the directory path (a slug
   under `~/.claude/projects`), so a renamed directory starts with an empty history, and any script
   or shortcut that hard-codes the old path breaks. Inferred from how that slug is derived, not
   tested. If you do rename it, run `git worktree repair` in the main clone when you keep
   worktrees.
6. **Post-rename commit** on a new branch (a normal `chore/` pull request):
   ```bash
   python scripts/rename_repo_urls.py --new tracegauge --check      # expect exit 1 and the list below
   python scripts/rename_repo_urls.py --new tracegauge --apply      # rewrites live files in place
   python scripts/rename_repo_urls.py --new tracegauge --check      # expect exit 0
   git diff --stat                                                  # only the "live" files below
   python -m pytest tests/test_check_readme_pypi.py tests/test_public_text.py
   ```
   Commit message suggestion: `chore: point links at the renamed repository`. The tests that pin the
   README (`tests/test_readme_examples.py`) do not contain the slug and are unaffected.

   **Recommended maximum gap between step 2 and this commit: the same day.** Have the branch ready
   before renaming (run `--apply` on it beforehand, but do not merge until the rename is done).
   During the gap GitHub's redirects cover every old `github.com` URL in the README and on PyPI;
   the one form not covered by the quoted page is the README's image URL (see the table below),
   so the badge is the thing to look at if the gap is longer.

   **Sibling places that link the old name** (outside this repository, so the script cannot see
   them; search each for `token-efficiency-scorer` and update after the rename):
   - the `adk-tracegauge` repository (README, `pyproject.toml` URLs, any pinned `git+https` install);
   - any portfolio page or profile README that links this repository or its badge;
   - the PyPI project page of the *previous* release (cannot be edited; only a new release changes it).
7. **Confirm no old URL remains in live files:**
   ```bash
   python scripts/rename_repo_urls.py --new tracegauge --check
   git grep -n "gaurav-gandhi-2411/token-efficiency-scorer" -- . ':!CHANGELOG.md' ':!docs/releases' ':!docs/audit' \
       ':!research' ':!data' ':!docs/RENAME_REPO.md' ':!CURRENT_STATE.md' ':!PLAN.md' ':!NEXT_PHASE.md' ':!spec.md'
   ```
   Both must print nothing for the slug. The historical files listed at the end of the inventory are
   skipped on purpose (see "What is not rewritten").
8. **Do not create a new repository called `token-efficiency-scorer`** later: GitHub warns that this breaks the
   redirects to the renamed repository.
9. PyPI shows the project links of the *latest release's* metadata. The links in `pyproject.toml`
   change on PyPI with the next release (0.16.0 or later); 0.15.0's page keeps the old links and
   relies on the redirect.

## What GitHub redirects and what it does not

From GitHub's page "Renaming a repository" (retrieved 2026-10-12,
https://docs.github.com/en/repositories/creating-and-managing-repositories/renaming-a-repository):

> When you rename a repository, all existing information, with the exception of project site URLs,
> is automatically redirected to the new name, including: Issues, Wikis, Stars, Followers

> In addition to redirecting web traffic, all `git clone`, `git fetch`, or `git push` operations
> targeting the previous location will continue to function as if made on the new location.
> However, to reduce confusion, we strongly recommend updating any existing local clones to point to
> the new repository URL.

> If you plan to rename a repository that has a GitHub Pages site, we recommend using a custom
> domain for your site. This ensures that the site's URL isn't impacted by renaming the repository.

> GitHub will not redirect calls to an action hosted by a renamed repository. Any workflow that uses
> that action will fail with the error `repository not found`.

> If you create a new repository under your account in the future, do not reuse the original name of
> the renamed repository. If you do, redirects to the renamed repository will no longer work.

Applied to this repository:

| Item | Effect of the rename |
|---|---|
| Issues, stars, followers, wiki, web URLs (blob, compare, issues) | Redirected, per the page above. |
| `git clone` / `fetch` / `push` to the old URL | Keep working; update with `git remote set-url`. |
| GitHub Pages | None configured (`gh api repos/gaurav-gandhi-2411/token-efficiency-scorer/pages` returned 404 on 2026-10-12), nothing to break. |
| GitHub Actions `uses:` of this repository | None: no workflow here uses this repository as an action (`grep -rn "uses: gaurav-gandhi-2411" .github` finds nothing), so the "not redirected" warning does not apply. |
| Image and badge links in the README | The README has no `raw.githubusercontent.com` link. Its one image is `https://github.com/<owner>/<repo>/raw/master/assets/brand/badge.svg` (README line 3, a `github.com/.../raw/...` form); the CI badge is `github.com/<owner>/<repo>/actions/workflows/ci.yml/badge.svg` and the licence badge links to `github.com/<owner>/<repo>/blob/master/LICENSE`. The quoted page does not mention the `/raw/` form specifically, so whether it keeps redirecting is UNVERIFIED; step 6 rewrites all of them. |
| Branch protection on `master` | Belongs to the repository and follows it. Today: required checks `lint-and-test`, `manifest-provenance`, `python-compat (every leg)`, `readme-pypi`; strict; enforced for admins; 0 required reviews. Check names are job names, not repository names, so they do not change. Re-run the `gh api` command in step 4 to confirm. |
| Environment `pypi` | Belongs to the repository; one required reviewer. Re-check in step 4. |
| Secrets and variables | None at repository level (`gh api .../actions/secrets` lists none). |
| Webhooks | None (`gh api .../hooks` returned an empty list). |
| Rulesets | None (`gh api .../rulesets` returned `[]`). |
| `.github/dependabot.yml`, issue templates | Contain no repository slug; unaffected. |
| Local clones and worktrees | Update the remote; see step 5. |

## PyPI Trusted Publisher: update BEFORE the next release

`.github/workflows/release.yml` publishes with PyPI Trusted Publishing (OpenID Connect, no token): the
`publish` job has `environment: pypi` and `permissions: id-token: write`. PyPI's documentation says a
GitHub Actions trusted publisher is registered with the repository owner, the repository name, the
workflow filename and, optionally, an environment name
(https://docs.pypi.org/trusted-publishers/adding-a-publisher/, retrieved 2026-10-12). After the
rename the repository named in the OIDC token is `gaurav-gandhi-2411/tracegauge`, so a publisher registered for
the old name no longer matches and the upload would be rejected. Expected behaviour from that
documentation; it has not been exercised, because a `workflow_dispatch` run never publishes.

On pypi.org, project `tracegauge`, "Publishing", add a GitHub publisher with exactly:

| Field | Value |
|---|---|
| Owner | `gaurav-gandhi-2411` |
| Repository name | `tracegauge` |
| Workflow name | `release.yml` |
| Environment name | `pypi` |

Keep the old publisher until the first release from the renamed repository has published
successfully, then remove it.

## What is not rewritten, and why

`scripts/rename_repo_urls.py` skips `CHANGELOG.md`, `docs/releases/`, `docs/audit/`, `research/`,
`data/` and the dated status notes (it never reads this document either, in any mode). Those record what was true when they were written,
and GitHub redirects the old blob, compare and issue URLs they contain, so the links keep working;
rewriting a published release entry would also change history. `--include-historical` rewrites them
if you decide otherwise. Bare mentions of the old name that are not links (prose, a directory name
inside a log) are reported as "manual" and never rewritten.

## Inventory (g## Inventory (generated)

Produced by `python scripts/rename_repo_urls.py --new tracegauge --inventory` on the commit that
last touched `scripts/rename_repo_urls.py`. This document itself is excluded from the scan
(`EXCLUDED_PATHS` in the script), so these numbers do not change when this text changes. Totals from
`--check` on the same commit: 46 old reference(s) in live files (45 `slug` rows and 1 `cd` row, all
rewritten by `--apply`); 3 bare mention(s) to review by hand (`scripts/efficiency_score.py` and
`scripts/task_classifier.py` docstrings, and the `DEFAULT_OLD` constant of the script itself); 294
mention(s) in the 13 skipped historical files listed at the end of the table, left as written.

| Where | Kind | Old | Replacement |
|---|---|---|---|
| `CONTRIBUTING.md:11` | slug | `gaurav-gandhi-2411/token-efficiency-scorer` | gaurav-gandhi-2411/tracegauge |
| `CONTRIBUTING.md:12` | cd | `cd token-efficiency-scorer` | cd tracegauge |
| `README.md:3` | slug | `gaurav-gandhi-2411/token-efficiency-scorer` | gaurav-gandhi-2411/tracegauge |
| `README.md:5` | slug | `gaurav-gandhi-2411/token-efficiency-scorer` | gaurav-gandhi-2411/tracegauge |
| `README.md:7` | slug | `gaurav-gandhi-2411/token-efficiency-scorer` | gaurav-gandhi-2411/tracegauge |
| `README.md:7` | slug | `gaurav-gandhi-2411/token-efficiency-scorer` | gaurav-gandhi-2411/tracegauge |
| `README.md:8` | slug | `gaurav-gandhi-2411/token-efficiency-scorer` | gaurav-gandhi-2411/tracegauge |
| `README.md:17` | slug | `gaurav-gandhi-2411/token-efficiency-scorer` | gaurav-gandhi-2411/tracegauge |
| `README.md:24` | slug | `gaurav-gandhi-2411/token-efficiency-scorer` | gaurav-gandhi-2411/tracegauge |
| `README.md:37` | slug | `gaurav-gandhi-2411/token-efficiency-scorer` | gaurav-gandhi-2411/tracegauge |
| `README.md:50` | slug | `gaurav-gandhi-2411/token-efficiency-scorer` | gaurav-gandhi-2411/tracegauge |
| `README.md:56` | slug | `gaurav-gandhi-2411/token-efficiency-scorer` | gaurav-gandhi-2411/tracegauge |
| `README.md:97` | slug | `gaurav-gandhi-2411/token-efficiency-scorer` | gaurav-gandhi-2411/tracegauge |
| `README.md:107` | slug | `gaurav-gandhi-2411/token-efficiency-scorer` | gaurav-gandhi-2411/tracegauge |
| `README.md:207` | slug | `gaurav-gandhi-2411/token-efficiency-scorer` | gaurav-gandhi-2411/tracegauge |
| `README.md:207` | slug | `gaurav-gandhi-2411/token-efficiency-scorer` | gaurav-gandhi-2411/tracegauge |
| `README.md:239` | slug | `gaurav-gandhi-2411/token-efficiency-scorer` | gaurav-gandhi-2411/tracegauge |
| `README.md:397` | slug | `gaurav-gandhi-2411/token-efficiency-scorer` | gaurav-gandhi-2411/tracegauge |
| `README.md:422` | slug | `gaurav-gandhi-2411/token-efficiency-scorer` | gaurav-gandhi-2411/tracegauge |
| `README.md:422` | slug | `gaurav-gandhi-2411/token-efficiency-scorer` | gaurav-gandhi-2411/tracegauge |
| `README.md:526` | slug | `gaurav-gandhi-2411/token-efficiency-scorer` | gaurav-gandhi-2411/tracegauge |
| `README.md:527` | slug | `gaurav-gandhi-2411/token-efficiency-scorer` | gaurav-gandhi-2411/tracegauge |
| `README.md:528` | slug | `gaurav-gandhi-2411/token-efficiency-scorer` | gaurav-gandhi-2411/tracegauge |
| `README.md:529` | slug | `gaurav-gandhi-2411/token-efficiency-scorer` | gaurav-gandhi-2411/tracegauge |
| `README.md:529` | slug | `gaurav-gandhi-2411/token-efficiency-scorer` | gaurav-gandhi-2411/tracegauge |
| `README.md:535` | slug | `gaurav-gandhi-2411/token-efficiency-scorer` | gaurav-gandhi-2411/tracegauge |
| `README.md:535` | slug | `gaurav-gandhi-2411/token-efficiency-scorer` | gaurav-gandhi-2411/tracegauge |
| `RELEASING.md:83` | slug | `gaurav-gandhi-2411/token-efficiency-scorer` | gaurav-gandhi-2411/tracegauge |
| `RELEASING.md:84` | slug | `gaurav-gandhi-2411/token-efficiency-scorer` | gaurav-gandhi-2411/tracegauge |
| `pyproject.toml:74` | slug | `gaurav-gandhi-2411/token-efficiency-scorer` | gaurav-gandhi-2411/tracegauge |
| `pyproject.toml:75` | slug | `gaurav-gandhi-2411/token-efficiency-scorer` | gaurav-gandhi-2411/tracegauge |
| `pyproject.toml:76` | slug | `gaurav-gandhi-2411/token-efficiency-scorer` | gaurav-gandhi-2411/tracegauge |
| `pyproject.toml:77` | slug | `gaurav-gandhi-2411/token-efficiency-scorer` | gaurav-gandhi-2411/tracegauge |
| `pyproject.toml:78` | slug | `gaurav-gandhi-2411/token-efficiency-scorer` | gaurav-gandhi-2411/tracegauge |
| `scripts/cc_score_runner.sh:171` | slug | `gaurav-gandhi-2411/token-efficiency-scorer` | gaurav-gandhi-2411/tracegauge |
| `scripts/efficiency_score.py:6` | bare | `token-efficiency-scorer` | (manual: not a link) |
| `scripts/gpu_score_runner.sh:151` | slug | `gaurav-gandhi-2411/token-efficiency-scorer` | gaurav-gandhi-2411/tracegauge |
| `scripts/gpu_score_runner_b2.sh:161` | slug | `gaurav-gandhi-2411/token-efficiency-scorer` | gaurav-gandhi-2411/tracegauge |
| `scripts/gpu_score_runner_b3_full.sh:143` | slug | `gaurav-gandhi-2411/token-efficiency-scorer` | gaurav-gandhi-2411/tracegauge |
| `scripts/gpu_score_runner_rescore.sh:186` | slug | `gaurav-gandhi-2411/token-efficiency-scorer` | gaurav-gandhi-2411/tracegauge |
| `scripts/rename_repo_urls.py:36` | bare | `token-efficiency-scorer` | (manual: not a link) |
| `scripts/task_classifier.py:6` | bare | `token-efficiency-scorer` | (manual: not a link) |
| `scripts/vm_manual_setup.sh:10` | slug | `gaurav-gandhi-2411/token-efficiency-scorer` | gaurav-gandhi-2411/tracegauge |
| `scripts/vm_startup.sh:36` | slug | `gaurav-gandhi-2411/token-efficiency-scorer` | gaurav-gandhi-2411/tracegauge |
| `scripts/vm_startup_b2.sh:37` | slug | `gaurav-gandhi-2411/token-efficiency-scorer` | gaurav-gandhi-2411/tracegauge |
| `scripts/vm_startup_b3.sh:39` | slug | `gaurav-gandhi-2411/token-efficiency-scorer` | gaurav-gandhi-2411/tracegauge |
| `scripts/vm_startup_rescore.sh:39` | slug | `gaurav-gandhi-2411/token-efficiency-scorer` | gaurav-gandhi-2411/tracegauge |
| `tracegauge/cost.py:227` | slug | `gaurav-gandhi-2411/token-efficiency-scorer` | gaurav-gandhi-2411/tracegauge |
| `tracegauge/web/templates/baseline_status.html:101` | slug | `gaurav-gandhi-2411/token-efficiency-scorer` | gaurav-gandhi-2411/tracegauge |
| `CHANGELOG.md` lines 450 | slug (historical, skipped) | | left as written |
| `CURRENT_STATE.md` lines 1, 438 | bare (historical, skipped) | | left as written |
| `PLAN.md` lines 1 | bare (historical, skipped) | | left as written |
| `data/cc_session_digests.jsonl` lines 1, 4 | bare/slug (historical, skipped) | | left as written |
| `data/corpus_pool/pool_adapted.jsonl` lines 4, 121, 122, 123, 124, 125, 126, 127, 128, 129, 130, 131, 132, 133, 134 | bare/slug (historical, skipped) | | left as written |
| `data/vm_startup.sh` lines 32 | slug (historical, skipped) | | left as written |
| `docs/audit/COMPETITIVE_GAP_ANALYSIS.md` lines 347 | bare (historical, skipped) | | left as written |
| `docs/audit/FINAL_STATE.md` lines 1, 14, 234, 235, 236, 237 | bare/slug (historical, skipped) | | left as written |
| `docs/audit/RELEASE_0_10_2.md` lines 106, 235, 344 | bare/slug (historical, skipped) | | left as written |
| `research/06-calibration.md` lines 35 | bare (historical, skipped) | | left as written |
| `research/07-ingestion.md` lines 322, 323, 337, 356 | bare (historical, skipped) | | left as written |
