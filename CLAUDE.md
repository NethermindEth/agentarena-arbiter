# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this
repository.

## Project Overview

The AgentArena arbiter is the competition's judge. During a task's submission window it stores the
findings each audit agent posts; at the deadline it groups duplicates with Gemini, judges validity
and severity with Claude, and writes the verdicts back to the AgentArena backend. It authenticates
agents by reading the backend's MongoDB directly and keeps its own state in a second database on
the same cluster. The service lives in `Demo/app/`; the repository root holds the docs and CI.
`Demo/README.md` documents the endpoints, the data model and the configuration; the root
`README.md` is older and partly describes a design that does not exist.

## Development Commands

```bash
cd Demo
uv sync                  # .venv from uv.lock; after editing pyproject.toml: uv lock, commit both
cp .env.example .env     # then fill in values
uv run python -m app.main          # serves on :8004 with reload
uv run pytest                      # needs a MongoDB at MONGODB_URL and the required settings
uv run ruff check .      # lint (add --fix to autofix)
uv run ruff format .     # format
uv run pyright           # type check; needs Node, downloaded on first run if absent
pre-commit install       # once per clone, from the repository root; runs the same locked tools
```

Settings are read when `app.config` is imported and the required ones have no defaults, so the
tests need `MONGODB_URL`, the three `BACKEND_*_ENDPOINT` values, `BACKEND_API_KEY`,
`CLAUDE_API_KEY` and `GEMINI_API_KEY` set, with placeholders for everything but MongoDB; see the
`tests` job in `.github/workflows/ci-tests.yaml`. The integration tests write to that MongoDB.

## Architecture

- **`app/main.py`**: the HTTP surface (`/process_findings`, `/test/process_findings`, the
  backend-key endpoints), the APScheduler job per approved task, and the deadline processing that
  downloads the repository, runs both LLM stages and posts the verdicts.
- **`app/core/deduplication.py`** and **`app/core/gemini_model.py`**: one structured Gemini call
  groups findings that describe the same bug.
- **`app/core/evaluation.py`** and **`app/core/claude_model.py`**: Claude judges each group or
  batch for validity and severity.
- **`app/database/mongodb_handler.py`**: Motor access to the arbiter's `security_findings`
  database and the backend's `agent_arena` database (agents and tasks).
- **`app/task_utils.py`**: downloads and extracts the task repository from the backend.
- **`app/config.py`**: pydantic-settings; `BACKEND_API_KEY` is the shared secret in both
  directions.

## Deployment

CI/CD via GitHub Actions; the cluster side (ArgoCD tracking `latest` by digest) is outside this
repository.
- Required checks on `staging`: `tests`, `lint`, `typecheck` (`ci-tests.yaml`) and
  `build-arbiter-staging / Build and publish Docker image` (`push-jfrog-staging.yaml`). On `main`:
  `build-arbiter-prod / Build and publish Docker image`. A workflow job only blocks merges once it
  is also listed in the branch rulesets in the repository settings; that step is manual.
- The image builds from `Demo/Dockerfile` through the shared `docker-build-push-jfrog` workflow
  (v5.1.1) for `linux/amd64`: a uv-built environment on the official Python image, copied onto a
  distroless runtime with no shell. An image push is gated by a JFrog Xray scan (watch
  `iac-all-repos`): a Critical vulnerability or a malicious package fails the job before the push.
  Pull requests and the build-only `main` run push nothing, so they are not scanned. A waiver is an
  Xray ignore rule scoped to `xray-scan-agentarena-arbiter`, owned by the platform team.
- Push to `staging` → builds and pushes `kyoto-oci-local-staging/agentarena-arbiter`.
- Push to `main` → release-please opens or updates the release PR; merging it tags the release and
  pushes the prod image as `vX.Y.Z` and `latest`. The version also lands in `Demo/app/main.py`. To
  rebuild a release, re-run its `Build and Push images on jfrog prod` run from the Actions tab; the
  re-run scans against today's Xray data, so a newly reported Critical needs a waiver or a new
  release.
- `staging` is the default branch and `main` is the release branch. `staging` merges by squash with
  the PR title as the commit title, so PR titles are Conventional Commits; `main` takes merge
  commits only (the `staging` to `main` promotion and the release PR). Dependabot opens version and
  security PRs against `staging`; see `.github/dependabot.yml`.
- AI code review runs when a PR comment contains `@review-pls`
  (`.github/workflows/code-review.yaml`); it is advisory and needs the `ANTHROPIC_API_KEY` and
  `OPENAI_API_KEY` secrets.
- `Demo/pyproject.toml` keeps `version = "0.0.0"` on purpose: uv.lock records it, and a release
  tool bumping it without `uv lock` would break every `uv sync --locked`.

## Notes on expected good practices

- Ruff checks the Python files against the rules in `Demo/pyproject.toml`. `ruff format` formats
  them at 100 columns and owns code wrapping and quotes; do not re-flow code by hand. `E501` is off
  because the formatter cannot wrap strings, URLs and comments; wrap those at 100 yourself.
- A deliberate exception to a Ruff rule is a `# noqa: CODE  reason` on the reported line, naming
  the constraint that makes the rule not apply there.
- Pyright checks the application in `standard` mode (`[tool.pyright]` in `Demo/pyproject.toml`);
  `tests/` is not type-checked. Fix a type error by narrowing the value or declaring the type the
  code relies on. A `# pyright: ignore[rule]  reason` is only for a third-party package whose types
  are incomplete; a bare `# type: ignore` is never used.
- Keep files from growing too big: split by separation of concerns, so that what a file holds is
  meaningful only for it; `Demo/app/main.py` is already the file most in need of this.
- Public functions must have a docstring explaining their purpose. Keep comments to the non-obvious
  "why", describing the final state rather than how it was reached. Punctuate comments with commas,
  semicolons and periods; avoid em dashes.
- Every `uses:` in a workflow is pinned to a full commit SHA with the version as a trailing comment;
  Dependabot's `github-actions-all` group keeps the SHAs current. Checkouts set
  `persist-credentials: false`.
- When needed, update the README files accordingly.
