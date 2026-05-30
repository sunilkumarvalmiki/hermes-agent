# Personal Hermes Agent Fork Workflow

This branch is the personal integration lane for testing Hermes Agent fixes before they land upstream. Keep `main` as a clean mirror of `NousResearch/hermes-agent/main`; use `integration/personal-stable` for tested rollups; use `topic/*` branches for individual upstream PRs or local fixes.

## Decision

Use a three-tier workflow:

1. `main`: exact upstream mirror. Do not add personal changes here.
2. `topic/<source>`: one candidate PR, local fix, or experiment per branch.
3. `integration/personal-stable`: only reviewed and tested changes intended for daily use.

Install or run from a pinned commit or tag, never from an unreviewed moving topic branch.

## Pros

- You can use important fixes before upstream maintainers merge them.
- The integration branch gives you a controlled place to test combinations of fixes.
- Keeping fork `main` clean makes upstream syncs simple and low-conflict.
- Topic branches keep root-cause debugging possible when a PR breaks behavior.
- Tags give rollback points for Windows, macOS, Linux, and Codespaces installs.

## Cons

- You own the merge debt until upstream catches up.
- Unmerged upstream PRs may be incomplete, stale, or unsafe.
- Multiple PRs can interact in ways none of the original authors tested.
- Codespaces proves Linux behavior only; native Windows/macOS still need separate smoke checks.
- A personal branch can drift into a private fork unless every patch is documented and regularly reconciled.

## Intake Procedure

Start clean:

```bash
git fetch upstream main --tags
git switch main
git merge --ff-only upstream/main
git push origin main
git switch integration/personal-stable
git merge --ff-only main || git merge --no-ff main
```

Import one candidate PR:

```bash
PR=35244
git fetch upstream "pull/${PR}/head:topic/upstream-pr-${PR}"
git switch "topic/upstream-pr-${PR}"
git rebase upstream/main
```

Review and test that topic branch first. If it survives, merge it into the integration branch with provenance:

```bash
git switch integration/personal-stable
git merge --no-ff "topic/upstream-pr-${PR}" -m "merge(pr-${PR}): import tested upstream fix"
```

If upstream later lands the same fix, compare before keeping or dropping your local version:

```bash
git fetch upstream main
git log --oneline --left-right --cherry-pick upstream/main...integration/personal-stable
git range-diff upstream/main...integration/personal-stable
```

## Required Gates Before Use

For any change that can affect runtime behavior:

- Reproduce the bug or desired behavior before editing.
- Add or identify a focused regression test before changing production code.
- Run the smallest relevant test first, then wider tests.
- Run the personal CI workflow on `integration/personal-stable`.
- Run CodeQL and supply-chain checks before tagging a personal release.
- For terminal, process, file, installer, or path changes, run the Windows footgun checker.
- For dashboard or browser-visible behavior, add Playwright coverage or manually verify through the browser with a recorded command log.

## Local Isolated Install

Do not reuse the installed Hermes checkout. Use this clone and a separate home:

```powershell
cd C:\Users\pchin\Workspace\hermes-agent-fork
$env:HERMES_HOME = "$HOME\.hermes-personal-stable"
uv venv .venv --python 3.11
uv pip install -e ".[all,dev]"
.\.venv\Scripts\python.exe -m hermes_cli.main --version
```

Do not symlink this branch over the existing global `hermes` command until the integration branch has passed its gates.

## Codespaces

Open Codespaces against `integration/personal-stable`. The devcontainer sets an isolated `HERMES_HOME` at `/workspaces/.hermes-personal-stable`, installs Python and Node dependencies, and avoids writing secrets into the repo.

Use Codespaces secrets for provider keys. Do not commit `.env`, OAuth tokens, session databases, or generated local state.

## GitHub Settings To Keep Enabled

- Branch protection or rulesets for `main` and `integration/personal-stable`.
- Required pull requests into `integration/personal-stable`.
- Required status checks once the personal workflow has run at least once.
- Dependabot alerts and Dependabot security updates.
- Secret scanning and push protection where available.
- CodeQL code scanning on integration branches.

## Release Rule

When `integration/personal-stable` is good enough to use, tag it:

```bash
git tag -a "personal/v0.15.2-hermes.1" -m "Personal stable rollup based on Hermes v0.15.2"
git push origin "personal/v0.15.2-hermes.1"
```

Record the upstream base SHA, imported PR numbers, test runs, and known risks in the tag or release notes.
