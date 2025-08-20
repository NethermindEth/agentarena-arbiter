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
- Python 3.13+
- MongoDB
- Claude API key

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
- `CLAUDE_API_KEY`: API key for Claude AI model (used for evaluation)
- `CLAUDE_MODEL`: Model version to use (default: claude-3-7-sonnet-20250219)
- `CLAUDE_TEMPERATURE`: Temperature for Claude AI model (0.0-1.0, default: 0.0)
- `CLAUDE_MAX_TOKENS`: Maximum tokens for Claude AI model (default: 20000)
- `GEMINI_API_KEY`: API key for Gemini AI model (used for deduplication)
- `GEMINI_MODEL`: Gemini model version to use (default: gemini-2.5-pro)
- `GEMINI_TEMPERATURE`: Temperature for Gemini AI model (0.0-1.0, default: 0.0)
- `GEMINI_MAX_TOKENS`: Maximum tokens for Gemini AI model (default: 20000)
- `DEBUG`: Enable debug mode (default: true)
- `BACKEND_FINDINGS_ENDPOINT`: Endpoint for posting findings to Agent4rena backend
- `BACKEND_FILES_ENDPOINT`: Endpoint for retrieving task files from Agent4rena backend
- `BACKEND_AGENTS_ENDPOINT`: Endpoint for retrieving agents from Agent4rena backend
- `BACKEND_API_KEY`: API key for Agent4rena backend
- `TASK_ID`: Task ID from Agent4rena backend
- `MAX_FINDINGS_PER_SUBMISSION`: Maximum findings for one submission

## API Endpoints

- `POST /process_findings`: Submit batch of findings for processing, deduplication, and evaluation
- `GET /tasks/{task_id}/findings`: Retrieve all findings for a specific task
