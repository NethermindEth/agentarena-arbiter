# Arbiter Agent Workflow

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
    - Severity: evaluates severity as `EvaluatedSeverity.LOW`, `MEDIUM`, or `HIGH`
- Results are applied as follows:
    - Valid findings → `Status.UNIQUE_VALID` with assigned category, category_id, and evaluated_severity
    - Invalid findings → `Status.DISPUTED` with category, category_id, and evaluated_severity set to `None`
- Each unique category receives a distinct **category_id** for tracking similar issues

<<<<<<< HEAD
=======
## Automated Smart Contract Vulnerability Analysis

The system includes a specialized evaluation module that:
- Uses a blockchain-focused prompt for Claude to analyze smart contract vulnerabilities
- Considers blockchain-specific context, impact on funds, exploitation difficulty
- Applies consistent processing of results with robust handling of edge cases
- Ensures data model consistency (e.g., disputed findings have no category or severity)
- Generates unique category IDs that group similar issues together

## Setup and Installation

### Prerequisites
- Python 3.8+
- MongoDB
- Claude API key

### Environment Setup
1. Create a `.env` file based on `.env.example`
2. Add your Claude API key to the `.env` file: `CLAUDE_API_KEY=your_api_key_here`
3. Configure MongoDB connection string (defaults to localhost if not specified)
4. Set desired similarity threshold (default: 0.8)

### Installation
```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

## Running Tests

### Process Findings Test
```bash
python -m app.test.test_process_findings
```

This test verifies the complete workflow with three scenarios:
1. First submission - all new findings
2. Second submission - mix of duplicate and new findings
3. Different agent submission - similar and unique findings

The test confirms the system's ability to:
- Detect duplicates within the same agent's submissions
- Recognize similar findings across different agents
- Properly mark findings as disputed when they are invalid
- Classify findings with appropriate status, category, and severity

**Note:** A valid Claude API key is required to run this test.

>>>>>>> 6a02f3b (Update finding format and add endpoints)
## Data Models

### Finding Input Format
```python
class Finding(BaseModel):
    title: str
    description: str
    severity: Severity  # Enum: HIGH or MEDIUM

class FindingInput(BaseModel):
    task_id: str
    agent_id: str
    findings: List[Finding]
```

### Finding in Database
```python
class FindingDB(Finding):
    agent_id: str
    status: Status  # Enum: PENDING, ALREADY_REPORTED, SIMILAR_VALID, UNIQUE_VALID, DISPUTED
    category: Optional[str]  # None for disputed/already_reported findings
    category_id: Optional[str]  # None for disputed/already_reported findings
    evaluated_severity: Optional[EvaluatedSeverity]  # Enum: LOW, MEDIUM, HIGH, None for disputed
    evaluation_comment: Optional[str]
    created_at: datetime
    updated_at: datetime
```

## Configuration

- `CLAUDE_API_KEY`: API key for Claude AI model
- `CLAUDE_MODEL`: Model version to use (default: claude-3-opus-20240229)
- `SIMILARITY_THRESHOLD`: Threshold for considering two findings as similar (0.0-1.0, default: 0.8)

## API Endpoints

- `POST /process_findings`: Submit batch of findings for processing, deduplication, and evaluation
- `GET /tasks/{task_id}/findings`: Retrieve all findings for a specific task

## LangChain Integration

The system uses LangChain to integrate with Claude AI models for:
- Finding similarity comparison
- Smart contract vulnerability evaluation

<<<<<<< HEAD
3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

## Running Tests

### Unit Tests
The project includes various unit tests to verify the functionality of individual components:

```bash

# Run specific test modules
# For example
python -m app.test.test_finding_deduplication
python -m app.test.test_cross_agent_comparison
```
=======
**Note:** The code has been updated to use the latest LangChain API (`ainvoke` instead of deprecated `arun` method).
>>>>>>> 6a02f3b (Update finding format and add endpoints)
