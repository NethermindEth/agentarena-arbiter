# Workflow
## Submission Process

- Agents submit findings to the AgentArena backend, not to the arbiter. The backend stores each one
  as `Status.PENDING`, and the arbiter reads them from the backend at the task's deadline.

## Self-Deduplication Process

- The system identifies duplicates within the same agent's submissions
- Similarity is determined by comparing title and description
- Similarity threshold is configurable via `SIMILARITY_THRESHOLD` environment variable (default: 0.8)
- Duplicates are marked as `Status.ALREADY_REPORTED` with explanatory evaluation comments

## Cross-Agent Comparison

- Non-duplicate findings are compared against findings with `Status.UNIQUE_VALID` or `Status.SIMILAR_VALID` from other agents
- Similar findings are marked as `Status.SIMILAR_VALID` and inherit attributes from the original finding
- All similar findings inherit the same **category**, **category_id**, and **evaluated_severity**
- When a finding is marked as similar, any related `UNIQUE_VALID` finding is recategorized as `SIMILAR_VALID`
- Findings with the same **category_id** are aggregated for reporting and analysis

## Final Evaluation

Findings are evaluated with a port of the **findings-triage** Claude Code skill: every distinct
issue gets an analyst who traces the code and writes a working PoC in its own sandbox, two
adversarial verifiers who attack that conclusion, and a judge who weighs the evidence
(`app/core/claude_code_triage.py`, prompts in `app/core/triage_prompts.py`).

```
per task:  build agent ─ installs deps + prebuilds a private copy of the checkout
per unit:  Analyst ─ code trace + PoC in its own sandbox
             ├─► Refute verifier   ─ tries to overturn it; re-runs and audits the PoC for rigging
             └─► Severity verifier ─ hunts escalations and mitigations; proves changes with a PoC
           Judge ─ weighs evidence, not votes; re-runs PoCs to settle factual disputes
```

- A **unit** is one finding, or one duplicate group: the group is merged into a digest by the
  aggregator (`app/core/claude_code_aggregator.py`), the original reports are attached, and the
  unit's verdict is applied to every member.
- Verdicts and severities are the skill's own (`VALID` / `PARTIALLY_VALID` / `INFORMATIONAL` /
  `INVALID`; Critical…Invalid rubric). The task's Q&A answers and additional docs are part of
  every agent's context (they define the trust model).
- Results: `VALID` / `PARTIALLY_VALID` keep their status, with `evaluated_severity` from the
  rubric (Critical is stored as `High`) and `evaluation_comment` set; `INVALID` →
  `Status.DISPUTED` with `evaluated_severity = Info`; `INFORMATIONAL` (true, no security impact)
  counts as valid `Info` only when the judge says a developer would change code because of it,
  otherwise it is disputed.
- No verdict is recorded without the judge. A unit whose agents fail (after retries) gets no
  verdict; the run then posts nothing and leaves the task unmarked, so the backend is unchanged
  and `POST /tasks/{task_id}/process` judges the whole task again. If the sandbox preflight
  fails, the run stops before downloading the repository or calling any model.
- Cost: 4 agents per unit plus one build agent per task, and one merge per duplicate group,
  each capped by `TRIAGE_AGENT_MAX_BUDGET_USD`; at most `TRIAGE_MAX_PARALLEL_AGENTS` of these
  Claude Code runs (merges included) go at once across the whole service. Finding
  titles/descriptions are capped before they reach any prompt, and so are a duplicate group's
  member reports (at most 10, about 60k characters in all; the prompt says how many were left
  out).

### Isolation (agents execute untrusted code)

The finding text is written by participants who want it approved, and the repository comes from
the sponsor, so every agent is treated as potentially hostile:

- Every run is `claude -p --bare --restricted --strict-mcp-config --permission-prompts none`:
  no hooks, CLAUDE.md, MCP servers, plugins or settings from the repository or the service user
  are loaded; only the tools a role needs are enabled; anything else is denied.
- The child gets a private `HOME`/`TMPDIR` and only the Anthropic key in its environment
  (`CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=1`, set for agents with Bash, also strips it from the
  commands they run).
- Commands run in the Claude Code **bubblewrap sandbox** with `failIfUnavailable` and
  `allowUnsandboxedCommands: false`: writes only inside the agent's own sandbox, no reads of the
  service user's HOME, the service directory, task data or anything else in the triage work
  directory (other agents, runs and evidence), no network for finding agents (package
  registries only for the build agent, via `strictAllowlist`). bubblewrap must be >= 0.8.0
  and not setuid (so nested user namespaces are disabled); Claude Code's "weaker nested
  sandbox" mode is not supported because agents could undo its write denies.
- Each agent works in its own full copy of the prebuilt checkout. PoCs move between agents only
  through the service, into a fresh copy with clean sources: only new code files under `test/`,
  `tests/`, `script/` or `poc/` directories, never configuration, dotfiles, test hooks
  (`conftest.py`, `build.rs`) or anything under `lib/` or `node_modules/`; regular files with
  plain names only, read through directory handles that never follow symlinks, size-capped.
  Other new files are listed to later agents as not staged, and edits to existing files are
  shown as a diff, never applied. So every verifier re-runs a PoC against unmodified code and
  the project's own configuration.
- Wall-clock timeouts kill the agent's whole process group, and any process an agent leaves
  running in its sandbox is killed before its files are read; the service process is marked
  non-dumpable so agents cannot read its environment through `/proc`.
- PoC evidence is kept under `TRIAGE_WORK_DIR/run-*/evidence/` for 30 days (older runs are
  deleted when a run starts); sandboxes are deleted after each run unless `DEBUG` is set.

**Host requirements.** PoCs need bubblewrap + socat and unprivileged user namespaces. On Ubuntu
24.04+ hosts, AppArmor blocks them unless bubblewrap's AppArmor profile is installed. Docker's
default seccomp profile blocks them too (see `docker-compose.yml`). With
`TRIAGE_POC_MODE=required` (default) a host that cannot sandbox leaves tasks unprocessed
rather than running PoCs unsandboxed; `optional` falls back to static analysis.
`TRIAGE_WORK_DIR` must be at most 48 bytes long: the CLI binds the sandbox's proxy sockets in
each agent's TMPDIR under it, and a Unix socket path is limited to 107 bytes (the preflight
checks it).
Use a dedicated, spend-limited Anthropic key for `CLAUDE_API_KEY`.

## Setup and Installation

### Prerequisites
- [uv](https://docs.astral.sh/uv/), which installs the pinned Python 3.14 itself
- Anthropic (`CLAUDE_API_KEY`) and Gemini API keys
- The Claude Code CLI on your `PATH` (`npm install -g @anthropic-ai/claude-code@2.1.328`); the
  Docker image bundles it
- For PoC execution: `bubblewrap`, `socat` and the project toolchains (e.g. Foundry) installed
  outside your HOME, which sandboxed commands cannot read (the Docker image puts Foundry in
  `/opt/foundry`); see "Host requirements" above

### Environment Setup
1. Create a `.env` file based on `.env.example`
2. Add your Claude API key to the `.env` file: `CLAUDE_API_KEY=your_api_key_here`
3. Set the `BACKEND_*` values to the AgentArena backend the arbiter judges for
4. Set desired similarity threshold (default: 0.8)

### Installation

#### Local Setup
```bash
# Navigate to Demo directory and install the locked dependencies into Demo/.venv
cd Demo
uv sync

# Start Application
uv run python -m app.main
```

Dependencies are declared in `pyproject.toml` and locked in `uv.lock`; after
changing a dependency, run `uv lock` and commit both files. The test tools, Ruff
and Pyright are in the `dev` group, which `uv sync` installs as well.

## Development checks

Ruff and Pyright run in CI (`.github/workflows/ci-tests.yaml`, jobs `lint` and
`typecheck`) with the versions locked in `uv.lock`; `pyproject.toml` holds their
settings. Run them locally the same way:

```bash
uv run ruff check .          # lint; add --fix to apply the safe fixes
uv run ruff format .         # format at 100 columns
uv run pyright               # type check (downloads Node on first run if absent)
```

`.pre-commit-config.yaml` at the repository root runs the same three on commit;
its header has the one-time setup.

## Running Tests

### 🚀 Running Tests

```bash
# Run all tests (uv sync already installed the test tools)
uv run pytest

# Run with coverage report
uv run pytest --cov=app --cov-report=term-missing
```

Settings are read at import time, so the required variables must be set;
placeholders do, since the LLM and backend calls are mocked.
`.github/workflows/ci-tests.yaml` lists them.

### 🧪 Writing Tests

#### Unit Tests
Test individual components in isolation with mocks:

```python
@pytest.mark.asyncio
async def test_process_findings_no_duplicates(deduplicator, sample_findings):
    with patch("app.core.deduplication.find_duplicates_structured") as mock:
        mock.return_value = []
        result = await deduplicator.process_findings("task", sample_findings, cache)
        assert result["summary"]["duplicates_found"] == 0
```

#### Integration Tests  
Test components working together:

```python
def test_get_task_findings_success(client):
    response = client.get("/tasks/task-1/findings", headers={"X-API-Key": "test-key"})
    assert response.status_code == 200
```

### 🐛 Debugging Tests

```bash
# Run specific test
uv run pytest tests/unit/test_models.py::TestFindingDB::test_creation

# Stop on first failure  
uv run pytest -x

# Drop to debugger on failure
uv run pytest --pdb

# Show full traceback
uv run pytest --tb=long
```

#### Docker Setup
```bash
# Build and start the services
docker-compose build
docker-compose up -d

# Check service status
docker-compose ps

# View logs
docker-compose logs -f
```

The API will be available at http://localhost:8004.

### Stopping Docker Services
```bash
docker-compose down
```

## API Usage

### Retrieve Findings
```bash
curl http://localhost:8004/tasks/test-task-1/findings | python -m json.tool
```

## Data Models

### Finding
```python
class Finding(BaseModel):
    title: str
    description: str
    severity: Severity  # Enum: HIGH, MEDIUM, LOW, or INFO
    file_paths: List[str]
```

### Finding in Database
```python
class FindingDB(Finding):
    agent_id: str
    status: Status  # Enum: PENDING, ALREADY_REPORTED, SIMILAR_VALID, BEST_VALID, UNIQUE_VALID, or DISPUTED
    category: Optional[str]  # None for disputed findings
    category_id: Optional[str]  # None for disputed findings
    evaluated_severity: Optional[Severity]  # Enum: HIGH, MEDIUM, LOW, or INFO
    evaluation_comment: Optional[str]
    created_at: datetime
    updated_at: datetime
```

## Configuration
The application requires the following environment variables. The triage's timeouts, the
build agent's network allowlist and the evidence retention are constants in
`app/core/claude_code_triage.py`: the allowlist is a sandbox boundary, so it changes through
code review.

- `CLAUDE_API_KEY`: Anthropic API key (passed to the `claude` subprocess as `ANTHROPIC_API_KEY`)
- `CLAUDE_MODEL`: Model id passed to `claude --model` (default: claude-opus-5-5)
- `CLAUDE_EFFORT`: Effort passed to `claude --effort` (default: high)
- `TRIAGE_POC_MODE`: `required` (default), `optional` or `off`; see Final Evaluation
- `TRIAGE_WORK_DIR`: sandboxes + PoC evidence; must be outside HOME, the service dir and task
  data, and at most 48 bytes long (default: `<system tmp>/arbiter-triage-<uid>`; the image uses
  `/var/lib/arbiter-triage`)
- `TRIAGE_MAX_PARALLEL_AGENTS`: Claude Code runs at once across the service, merges included
  (default 4)
- `TRIAGE_AGENT_MAX_BUDGET_USD`: spend cap per `claude` run (default 10, 0 = no cap)
- `GEMINI_API_KEY`: API key for Gemini AI model (used for deduplication)
- `GEMINI_MODEL`: Gemini model version to use (default: `gemini-3.5-flash`)
- `GEMINI_MAX_TOKENS`: Maximum output tokens for Gemini AI model (default: 65536)
- `GEMINI_THINKING_LEVEL`: Optional thinking level (`low`/`medium`/`high`). Used only for thinking models (e.g. `gemini-3.5-flash`); ignored with a warning for other Gemini models.
- `GEMINI_TEMPERATURE`: Optional temperature for Gemini (0.0-2.0). Unset by default. Used only for non-thinking Gemini models; ignored with a warning for thinking models (e.g. `gemini-3.5-flash`).
- `DEBUG`: keep each triage agent's sandbox after a run, for debugging (default: false; each is a
  full repository copy, so this fills the disk quickly)
- `LOG_LEVEL`: Set logging level (default: INFO)
- `BACKEND_API_KEY`: API key for AgentArena backend
- `BACKEND_FINDINGS_ENDPOINT`: The backend's `/api/insert-findings` endpoint. A run's only write: once deduplication and evaluation have decided every finding in memory, the arbiter posts all of the task's findings with their verdicts in one request, so a run that fails earlier leaves the backend unchanged and a retry judges all the findings again
- `BACKEND_TASK_REPOSITORY_ENDPOINT`: Endpoint for retrieving task repositories from AgentArena backend
- `BACKEND_APPROVED_TASKS_ENDPOINT`: The backend's `/api/arbiter/approved-tasks` endpoint, which lists the approved tasks to schedule; called with `X-API-Key: BACKEND_API_KEY`
- `BACKEND_TASKS_ENDPOINT`: The backend's `/api/arbiter/tasks` endpoint, called with the same `X-API-Key` header. The arbiter reads one task at `/{task_id}` and reads its findings at `GET /{task_id}/findings` (query filters `agent_id`, `since_timestamp`). It keeps its processed-task marker in the task's metadata, read with `GET /{task_id}/metadata` and replaced with `PUT /{task_id}/metadata`; the arbiter has no database of its own.

## API Endpoints

All endpoints except `/` need `X-API-Key: BACKEND_API_KEY`.

- `GET /tasks/{task_id}/findings`: Retrieve all findings for a specific task
- `POST /tasks/{task_id}/process`: Process a task now instead of at its deadline. A run takes
  hours, so it starts in the background and the answer is `202` with `"status": "accepted"`;
  the run posts the verdicts and marks the task processed when it completes. A failed run only
  shows in the arbiter's log and leaves the task unmarked, so it can be triggered again. Other
  answers: `200` with `"status": "already_processed"` or `"no_findings"`, `404` for a task the
  backend does not know, and `409` while the task is being processed, by an earlier trigger or
  its scheduled job (the scheduled job likewise skips a task a manual run is processing).
  Shutting the service down cancels the runs in progress, and their tasks stay unmarked.
- `POST /schedule-task/{task_id}`: Schedule a task's processing at its deadline
