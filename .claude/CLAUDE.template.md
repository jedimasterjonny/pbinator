# Template conventions

This file is managed by [python-template](https://github.com/jedimasterjonny/python-template) and refreshed on `just update-template`. Do not edit — put project-specific rules in `CLAUDE.local.md`.

## Layout

- Source: `src/<your-package>/`
- Tests: `tests/` (importlib mode; `from <your-package>.x import y`, no relative imports)
- Settings: `src/<your-package>/settings.py` (pydantic-settings); never read `os.environ` directly
- Secrets / config: `.env` (gitignored); template in `.env.example`

## Tooling

- **uv** is the only package manager. Add deps with `uv add <pkg>` (or `uv add --group dev <pkg>` for dev). Never hand-edit `[project.dependencies]` or run `pip`.
- **just** wraps the canonical commands. Discover with `just`. Run `just check` (lint + fmt-check + typecheck + test) before committing.
- **ruff** is configured with `select = ["ALL"]` + preview rules. Don't add `# noqa` without a justifying comment.
- **ty** runs in strict mode with all warn/ignore-default rules escalated to error.
- **pytest** enforces 100% branch coverage, `filterwarnings = ["error"]`, a 10s per-test timeout, and **network is blocked** (`pytest-socket`). Mock or use `pytest.mark.enable_socket` deliberately.
- **pre-commit** runs hygiene + ruff + ty on commit, pytest on push, and conventional-commits on the commit message.

## Workflow

1. `uv sync --locked` after pulling.
2. Make changes; keep `just check` green.
3. Commit with [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `chore:`, etc.).
4. Push — pytest runs in the pre-push hook and again in CI.

## Commit hygiene

Commits must be surgical. Each one does **one thing**:

- One purpose per commit. A bug fix and a refactor are two commits, not one.
- **No drive-by changes.** Don't sneak in unrelated whitespace, import reordering, formatter churn, renamed variables, or "while I was here" tweaks. If you spot something, commit it separately or leave it.
- If `ruff format` or `ruff check --fix` touches files outside the change you're making, split those into their own `style:` or `chore:` commit before the substantive one.
- Prefer many small commits over one large one. Reviewers (and `git bisect`) will thank you.
- Stage hunks deliberately (`git add -p`) rather than `git add -A`.

## Suppression discipline

Lint, type, and coverage suppressions are escape hatches, not defaults.

- Never add `# noqa`, `# type: ignore`, or `# pragma: no cover` without an inline comment explaining *why* on the same or preceding line.
- Don't disable rules at the project level (`pyproject.toml`) to silence a finding. Fix the code.
- `ty` is configured with `unused-ignore-comment = "error"` — stale ignores fail CI.

## Test discipline

- Tests cover **behaviour**, not lines. Write the test that proves the change is correct; coverage is a side effect, not a target.
- Never write a trivial assertion (e.g. `assert module is not None`) just to lift coverage. If a branch is genuinely uncoverable, exclude it deliberately via `[tool.coverage.report] exclude_also` with justification.
- Network is blocked in tests; mock external calls. Don't reach for `pytest.mark.enable_socket` casually.

## Never bypass guard rails

- No `git commit --no-verify`, no `--no-gpg-sign`, no skipping pre-commit / pre-push hooks.
- No `git rebase --no-edit` or other shortcuts that obscure history.
- Read the diff before staging. Avoid `git add -A` / `git commit -a`; prefer `git add -p` or named paths.
- If a hook or check fails, fix the cause — don't silence it.

## Use uv exclusively

- Run everything through `uv run <cmd>` (or via `just`, which wraps it). Never activate the venv manually, never call `python` directly, never `pip install`.
- Add deps with `uv add` / `uv add --group dev`. Update the lockfile with `uv lock`. The `uv-lock` pre-commit hook enforces lockfile freshness.

## Ask, don't guess

When an instruction is ambiguous — which file, which abstraction, which of two reasonable interpretations — ask one clarifying question before writing code. A plausible-but-wrong implementation is more expensive than a 10-second exchange.

## Updating from the template

Run `just update-template` to merge the latest `python-template` changes. Files under `.claude/CLAUDE.local.md` and your own source/tests are preserved.
