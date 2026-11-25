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
- MongoDB
- A LiteLLM server serving the Claude and Gemini models, and an API key for it

### Environment Setup
1. Create a `.env` file based on `.env.example`
2. Set `LITELLM_SERVER_URL` and `LITELLM_API_KEY` in the `.env` file
3. Configure MongoDB connection string (defaults to localhost if not specified)
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

Settings are read at import time, so the required variables must be set, with
placeholders for everything but MongoDB; `.github/workflows/ci-tests.yaml` lists
them. The integration tests write to the MongoDB at `MONGODB_URL`.

`tests/live/` is skipped unless `LITELLM_LIVE_TESTS=1`. It makes real calls through the LiteLLM
server, one per model and structured-output method, and is the check to run after changing a
model, the LiteLLM server or a result schema:

```bash
LITELLM_LIVE_TESTS=1 LITELLM_SERVER_URL=https://... LITELLM_API_KEY=... uv run pytest tests/live -v
```

### Prerequisites for Testing
- A MongoDB at `MONGODB_URL`; every other setting takes a placeholder, the LLM and
  backend calls are mocked

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
def test_api_endpoint_success(client):
    response = client.post("/process_findings", headers={"X-API-Key": "test"}, json=data)
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
- `LITELLM_SERVER_URL`: Base URL (`http://...` or `https://...`) of the LiteLLM server that serves both models through its OpenAI-compatible API
- `LITELLM_API_KEY`: LiteLLM API key, sent in the `X-LiteLLM-API-Key` header
- `LITELLM_TIMEOUT_SECONDS`: Seconds to wait for one model reply (default: 1800). Replies are not streamed, so this bounds the whole reply; size it for the longest evaluation batch (up to `EVALUATION_MAX_TOKENS` of output). Set it slightly above the LiteLLM server's own `request_timeout` (per model or global; LiteLLM's default is 6000 seconds), and bound the wait with the server's value. That way the proxy times out first, ends its upstream call and returns an error, and the client's retry starts a fresh call. With the client timeout below the server's, the client disconnects and retries while the proxy may still be generating the first reply, unless the deployment cancels upstream calls on client disconnect, so the same long generation can be billed twice. Connecting to the server times out after 10 seconds.
- `LITELLM_MAX_RETRIES`: Retries after a timeout, connection error, 429 or 5xx from LiteLLM (default: 3). Each retry repeats the whole call, so a run can wait up to (retries + 1) × `LITELLM_TIMEOUT_SECONDS` per call.
- `EVALUATION_MODEL`: LiteLLM model key used for evaluation (Claude) (default: `anthropic/claude-opus-4-8`)
- `EVALUATION_MAX_TOKENS`: Maximum tokens for Claude AI model (default: 60000)
- `EVALUATION_TEMPERATURE`: Optional temperature for Claude (0.0-1.0). Unset by default. Ignored with a warning for extended-thinking models (`claude-opus-4-7`, `claude-opus-4-8`).
- `DEDUPLICATION_MODEL`: LiteLLM model key used for deduplication (Gemini) (default: `gemini/gemini-3.8-flash`)
- `DEDUPLICATION_MAX_TOKENS`: Maximum output tokens for Gemini AI model (default: 65536)
- `DEDUPLICATION_THINKING_LEVEL`: Optional thinking level (`low`/`medium`/`high`), sent to LiteLLM as the reasoning effort. Used only for thinking models (`gemini-3*`); ignored with a warning for other Gemini models.
- `DEDUPLICATION_TEMPERATURE`: Optional temperature for Gemini (0.0-2.0). Unset by default. Used only for non-thinking Gemini models; ignored with a warning for thinking models (`gemini-3*`).
- `DEBUG`: Enable debug mode (default: true)
- `LOG_LEVEL`: Set logging level (default: INFO)
- `BACKEND_API_KEY`: API key for AgentArena backend
- `BACKEND_FINDINGS_ENDPOINT`: Endpoint for posting findings to AgentArena backend
- `BACKEND_SUBMISSIONS_ENDPOINT`: Endpoint for posting submissions to AgentArena backend
- `BACKEND_TASK_REPOSITORY_ENDPOINT`: Endpoint for retrieving task repositories from AgentArena backend

## API Endpoints

- `POST /process_findings`: Submit batch of findings for processing, deduplication, and evaluation
- `GET /tasks/{task_id}/findings`: Retrieve all findings for a specific task
