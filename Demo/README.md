# Workflow
## Submission Process

- When a new finding is submitted, the system adds these additional fields:
    - **status** (initialized as `Status.PENDING`)
    - **evaluation_comment** (initially empty)

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
  follows `TRIAGE_INFORMATIONAL_POLICY`: by default it counts as valid `Info` only when the judge
  says a developer would change code because of it, otherwise it is disputed.
- No verdict is recorded without the judge. A unit whose agents fail (after retries) gets no
  verdict: its findings go back to `PENDING`, nothing is posted, and the task is not marked
  processed; verdicts for the other units are kept, so `POST /tasks/{task_id}/process`
  re-evaluates only the leftovers. If the sandbox preflight fails, nothing is touched at all.
- Cost: 4 agents per unit plus one build agent per task, each capped by
  `TRIAGE_AGENT_MAX_BUDGET_USD`; at most `TRIAGE_MAX_PARALLEL_AGENTS` run at once across the
  whole service. Finding titles/descriptions are capped before they reach any prompt.

### Isolation (agents execute untrusted code)

The finding text is written by participants who want it approved, and the repository comes from
the sponsor, so every agent is treated as potentially hostile:

- Every run is `claude -p --bare --restricted --strict-mcp-config --permission-prompts none`:
  no hooks, CLAUDE.md, MCP servers, plugins or settings from the repository or the service user
  are loaded; only the tools a role needs are enabled; anything else is denied.
- The child gets a private `HOME`/`TMPDIR` and only the Anthropic key in its environment
  (`CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=1` also strips it from commands the agent runs).
- Commands run in the Claude Code **bubblewrap sandbox** with `failIfUnavailable` and
  `allowUnsandboxedCommands: false`: writes only inside the agent's own sandbox, no reads of the
  service user's HOME, the service directory, task data or anything else in the triage work
  directory (other agents, runs and evidence), no network for finding agents (package
  registries only for the build agent, via `strictAllowlist`). bubblewrap must be >= 0.8.0
  and not setuid (so nested user namespaces are disabled); Claude Code's "weaker nested
  sandbox" mode is not supported because agents could undo its write denies.
- Each agent works in its own full copy of the prebuilt checkout. PoCs move between agents only
  through the service (new regular files with plain names only, read through directory handles
  that never follow symlinks, size-capped), into a fresh copy with clean sources; edits to
  existing files are shown as a diff, never applied. So every verifier re-runs a PoC against
  unmodified code.
- Wall-clock timeouts kill the agent's whole process group, and any process an agent leaves
  running in its sandbox is killed before its files are read; the service process is marked
  non-dumpable so agents cannot read its environment through `/proc`.
- PoC evidence is kept under `TRIAGE_WORK_DIR/<run>/evidence/`; sandboxes are deleted.

**Host requirements.** PoCs need bubblewrap + socat and unprivileged user namespaces. On Ubuntu
24.04+ hosts, AppArmor blocks them unless bubblewrap's AppArmor profile is installed. Docker's
default seccomp profile blocks them too (see `docker-compose.yml`). With
`TRIAGE_POC_MODE=required` (default) a host that cannot sandbox leaves tasks unprocessed
rather than running PoCs unsandboxed; `optional` falls back to static analysis.
Use a dedicated, spend-limited Anthropic key for `CLAUDE_API_KEY`.

## Setup and Installation

### Prerequisites
- Python 3.13+
- MongoDB
- Anthropic API key (`CLAUDE_API_KEY`)
- The Claude Code CLI on your `PATH` (`npm install -g @anthropic-ai/claude-code@2.1.328`) — the
  Docker image below bundles it
- For PoC execution: `bubblewrap`, `socat` and the project toolchains (e.g. Foundry) — see
  "Host requirements" above

### Environment Setup
1. Create a `.env` file based on `.env.example`
2. Add your Claude API key to the `.env` file: `CLAUDE_API_KEY=your_api_key_here`
3. Configure MongoDB connection string (defaults to localhost if not specified)
4. Set desired similarity threshold (default: 0.8)

### Installation

#### Local Setup
```bash
# Navigate to Demo directory and activate virtual environment
cd Demo
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
python -m pip install -r requirements.txt

# Start Application
python -m app.main
```

## Running Tests

### 🚀 Running Tests

```bash
# Install test dependencies
pip install -r requirements-test.txt

# Run all tests
python -m pytest

# Run with coverage report
python -m pytest tests/ --cov=app --cov-report=term-missing
```

### Prerequisites for Testing
- MongoDB connection (local or Atlas)
- Gemini API key (for deduplication tests)
- Claude API key (for evaluation tests)

### 🧪 Writing Tests

#### Unit Tests
Test individual components in isolation with mocks:

```python
@pytest.mark.asyncio
async def test_process_findings_no_duplicates(deduplicator, sample_findings):
    with patch('app.core.deduplication.find_duplicates_structured') as mock:
        mock.return_value = []
        result = await deduplicator.process_findings("task", sample_findings, cache)
        assert result["summary"]["duplicates_found"] == 0
```

#### Integration Tests  
Test components working together:

```python
def test_api_endpoint_success(client):
    response = client.post("/process_findings", headers={"X-API-Key": "test"}, json=data)
    assert response.status_code == 200
```

### 🐛 Debugging Tests

```bash
# Run specific test
python -m pytest tests/unit/test_models.py::TestFindingDB::test_creation

# Stop on first failure  
python -m pytest -x

# Drop to debugger on failure
python -m pytest --pdb

# Show full traceback
python -m pytest --tb=long
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

**Note:** When running with Docker, you need to configure the MongoDB connection string in your `.env` file. This allows you to use either a local MongoDB instance or a MongoDB Atlas cluster.

### Stopping Docker Services
```bash
docker-compose down
```

## API Usage

### Submit Findings
```bash
curl -X POST http://localhost:8004/process_findings \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key" \
  -d '{
    "task_id": "test-task-1",
    "findings": [
      {
        "title": "Integer Overflow",
        "description": "Function X is vulnerable to integer overflow",
        "severity": "HIGH",
        "file_paths": ["contracts/Contract.sol", "contracts/Token.sol"]
      }
    ]
  }'
```

### Retrieve Findings
```bash
curl http://localhost:8004/tasks/test-task-1/findings | python -m json.tool
```

## Data Models

### Finding Input Format
```python
class Finding(BaseModel):
    title: str
    description: str
    severity: Severity  # Enum: HIGH, MEDIUM, LOW, or INFO
    file_paths: List[str]

class FindingInput(BaseModel):
    task_id: str
    findings: List[Finding]
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
The application requires the following environment variables:

- `MONGODB_URL`: MongoDB connection string (default: mongodb://localhost:27017)
- `CLAUDE_API_KEY`: Anthropic API key (passed to the `claude` subprocess as `ANTHROPIC_API_KEY`)
- `CLAUDE_MODEL`: Model id passed to `claude --model` (default: claude-opus-5-5)
- `CLAUDE_EFFORT`: Effort passed to `claude --effort` (default: high)
- `CLAUDE_CMD`: Claude Code CLI binary name/path (default: claude)
- `TRIAGE_POC_MODE`: `required` (default), `optional` or `off` — see Final Evaluation
- `TRIAGE_INFORMATIONAL_POLICY`: `actionable` (default), `valid` or `disputed` — how INFORMATIONAL
  rulings are recorded (see Final Evaluation)
- `TRIAGE_WORK_DIR`: sandboxes + PoC evidence; must be outside HOME, the service dir and task
  data (default: `<system tmp>/arbiter-triage-<uid>`; the image uses `/var/lib/arbiter-triage`)
- `TRIAGE_MAX_PARALLEL_AGENTS` (default 4), `TRIAGE_AGENT_TIMEOUT_SECONDS` (default 1800),
  `TRIAGE_BUILD_TIMEOUT_SECONDS` (default 2700), `TRIAGE_AGENT_MAX_BUDGET_USD` (default 10, 0 = no cap)
- `TRIAGE_BUILD_ALLOWED_DOMAINS`: comma-separated hosts the build agent may reach
- `TRIAGE_EXTRA_READ_PATHS`: toolchain dirs inside the service user's HOME that sandboxed
  commands may read (local runs, e.g. `~/.foundry`); never list directories with credentials
- `TRIAGE_KEEP_SANDBOXES`: keep per-agent sandboxes after a run (debugging)
- `GEMINI_API_KEY`: API key for Gemini AI model (used for deduplication)
- `GEMINI_MODEL`: Gemini model version to use (default: gemini-2.5-pro)
- `GEMINI_TEMPERATURE`: Temperature for Gemini AI model (0.0-1.0, default: 0.0)
- `GEMINI_MAX_TOKENS`: Maximum tokens for Gemini AI model (default: 20000)
- `DEBUG`: Enable debug mode (default: true)
- `LOG_LEVEL`: Set logging level (default: INFO)
- `BACKEND_API_KEY`: API key for AgentArena backend
- `BACKEND_FINDINGS_ENDPOINT`: Endpoint for posting findings to AgentArena backend
- `BACKEND_SUBMISSIONS_ENDPOINT`: Endpoint for posting submissions to AgentArena backend
- `BACKEND_TASK_REPOSITORY_ENDPOINT`: Endpoint for retrieving task repositories from AgentArena backend

## API Endpoints

- `POST /process_findings`: Submit batch of findings for processing, deduplication, and evaluation
- `GET /tasks/{task_id}/findings`: Retrieve all findings for a specific task
