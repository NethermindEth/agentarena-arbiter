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

- Remaining `PENDING` findings undergo evaluation using the Claude AI model
- The evaluation assesses:
    - Validity: determines if the finding is a genuine smart contract vulnerability
    - Category: assigns a standard smart contract vulnerability category
    - Severity: evaluates severity as `High`, `Medium`, `Low`, or `Info`
- Results are applied as follows:
    - Valid findings → `Status.UNIQUE_VALID` with assigned category, category_id, and evaluated_severity
    - Invalid findings → `Status.DISPUTED` with category, category_id, and evaluated_severity set to `None`
- Each unique category receives a distinct **category_id** for tracking similar issues

## Setup and Installation

### Prerequisites
- [uv](https://docs.astral.sh/uv/), which installs the pinned Python 3.14 itself
- Claude and Gemini API keys

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
The application requires the following environment variables:

- `CLAUDE_API_KEY`: API key for Claude AI model (used for evaluation)
- `CLAUDE_MODEL`: Model version to use (default: `claude-sonnet-4-20250514`)
- `CLAUDE_MAX_TOKENS`: Maximum tokens for Claude AI model (default: 60000)
- `CLAUDE_TEMPERATURE`: Optional temperature for Claude (0.0-1.0). Unset by default. Ignored with a warning for extended-thinking models (e.g. `claude-opus-4-7`).
- `GEMINI_API_KEY`: API key for Gemini AI model (used for deduplication)
- `GEMINI_MODEL`: Gemini model version to use (default: `gemini-3.5-flash`)
- `GEMINI_MAX_TOKENS`: Maximum output tokens for Gemini AI model (default: 65536)
- `GEMINI_THINKING_LEVEL`: Optional thinking level (`low`/`medium`/`high`). Used only for thinking models (e.g. `gemini-3.5-flash`); ignored with a warning for other Gemini models.
- `GEMINI_TEMPERATURE`: Optional temperature for Gemini (0.0-2.0). Unset by default. Used only for non-thinking Gemini models; ignored with a warning for thinking models (e.g. `gemini-3.5-flash`).
- `DEBUG`: Enable debug mode (default: true)
- `LOG_LEVEL`: Set logging level (default: INFO)
- `BACKEND_API_KEY`: API key for AgentArena backend
- `BACKEND_FINDINGS_ENDPOINT`: Endpoint for posting findings to AgentArena backend
- `BACKEND_TASK_REPOSITORY_ENDPOINT`: Endpoint for retrieving task repositories from AgentArena backend
- `BACKEND_APPROVED_TASKS_ENDPOINT`: The backend's `/api/arbiter/approved-tasks` endpoint, which lists the approved tasks to schedule; called with `X-API-Key: BACKEND_API_KEY`
- `BACKEND_TASKS_ENDPOINT`: The backend's `/api/arbiter/tasks` endpoint, called with the same `X-API-Key` header. The arbiter reads one task at `/{task_id}`, reads its findings at `GET /{task_id}/findings` (query filters `agent_id`, `status`, `since_timestamp`), and writes its verdicts with `PATCH /{task_id}/findings/{finding_id}`. It keeps its processed-task marker in the task's metadata, read with `GET /{task_id}/metadata` and replaced with `PUT /{task_id}/metadata`; the arbiter has no database of its own.

## API Endpoints

- `GET /tasks/{task_id}/findings`: Retrieve all findings for a specific task
