# Arbiter Agent Workflow

## Submission Process

- When a new finding is submitted, the system adds these additional fields:
    - **submission_id** (assigned automatically)
    - **status** (initialized as `Status.PENDING`)
    - **evaluation_comment** (initially empty)

## Self-Deduplication Process

- The system identifies duplicates within the same agent's submissions
- Similarity is determined by comparing title, description, recommendation, and code references
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

## Data Models

### Finding Input Format
```python
class FindingInput(BaseModel):
    project_id: str
    reported_by_agent: str
    finding_id: str
    title: str
    description: str
    severity: Severity  # Enum: HIGH or MEDIUM
    recommendation: str
    code_references: List[str]
```

### Finding in Database
```python
class FindingDB(BaseModel):
    project_id: str
    reported_by_agent: str
    finding_id: str
    title: str
    description: str
    severity: Severity  # Original reported severity
    recommendation: str
    code_references: List[str]
    submission_id: int
    status: Status  # Enum: PENDING, ALREADY_REPORTED, SIMILAR_VALID, UNIQUE_VALID, DISPUTED
    category: Optional[str]  # None for disputed findings
    category_id: Optional[str]  # None for disputed findings
    evaluated_severity: Optional[EvaluatedSeverity]  # Enum: LOW, MEDIUM, HIGH, None for disputed
    evaluation_comment: Optional[str]
```

## Configuration

- `CLAUDE_API_KEY`: API key for Claude AI model
- `SIMILARITY_THRESHOLD`: Threshold for considering two findings as similar (0.0-1.0, default: 0.8)

## Setup and Installation

### Prerequisites
- Python 3.8+
- MongoDB instance
- Anthropic API key for Claude

### Environment Setup
1. Create a `.env` file in the project root with the following variables:
   ```
   CLAUDE_API_KEY=your_api_key_here
   MONGODB_URI=mongodb://localhost:27017
   SIMILARITY_THRESHOLD=0.8
   ```

2. Create and activate a virtual environment:
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   ```

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
