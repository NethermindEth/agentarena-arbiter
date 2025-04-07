# Vulnerability Submissions API

An API endpoint for security audit agents to submit vulnerability findings for smart contract audits. This API integrates directly with the cross-agent comparison system to process, deduplicate, and classify security findings.

## Overview

This API provides a standardized interface for audit agents to submit security vulnerabilities they discover during smart contract audits. Upon submission, each finding is:

1. Validated against the project database to ensure it references an existing project
2. Processed through the cross-agent comparison system
3. Checked for duplicates against previous submissions
4. Compared with findings from other agents to identify similar issues
5. Classified and stored with appropriate metadata

## Setup & Installation

1. **Install dependencies**

```bash
pip install fastapi uvicorn pymongo python-dotenv pydantic
```

2. **Create a `.env` file**

In the project directory, create a `.env` file with the following configuration:

```
# MongoDB connection
MONGO_URI=mongodb://localhost:27017

# Cross-agent comparison settings
SIMILARITY_THRESHOLD=0.8
```

3. **Ensure MongoDB is running**

The API requires MongoDB for project validation and finding storage.

```bash
# Check if MongoDB is running
mongo --eval "db.version()" || mongosh --eval "db.version()"
```

4. **Integrate with arbiter agent code**

This API is designed to be integrated directly with the arbiter agent codebase. Ensure that the arbiter agent's modules (particularly `app.core.cross_agent_comparison`) are available in the Python path.

5. **Start the API server**

```bash
python -m uvicorn vulnerability_submissions:app --reload
```

This will start the server on http://127.0.0.1:8001

## API Documentation

Once the server is running, interactive API documentation is available at:

- http://127.0.0.1:8001/docs

## API Endpoints

### Submit a vulnerability

```
POST /api/vulnerabilities
```

**Request Body:**

```json
{
  "project_id": "project-123",
  "reported_by_agent": "agent-456",
  "finding_id": "finding-789",
  "title": "Reentrancy vulnerability in withdraw function",
  "description": "The withdraw function allows state changes after external calls, creating a potential reentrancy condition where an attacker can drain funds.",
  "severity": "HIGH",
  "recommendation": "Implement the checks-effects-interactions pattern by updating balances before making external calls.",
  "code_references": ["contracts/Vault.sol:24", "contracts/Vault.sol:42"]
}
```

**Response:**

```json
{
  "message": "Vulnerability submitted and processed successfully",
  "processing_results": {
    "deduplication": {
      "new_findings": 1,
      "duplicates": 0,
      "new_ids": ["finding-789"]
    },
    "cross_comparison": {
      "total": 1,
      "similar_valid": 0,
      "pending_evaluation": 1,
      "already_reported": 0,
      "similar_ids": [],
      "pending_ids": ["finding-789"]
    }
  }
}
```

### Health check

```
GET /health
```

**Response:**

```json
{
  "status": "healthy"
}
```

## Security Finding Model

Security findings must include the following fields:

| Field             | Description                              |
| ----------------- | ---------------------------------------- |
| project_id        | Identifier of the audited project        |
| reported_by_agent | Identifier of the audit agent            |
| finding_id        | Unique identifier for the finding        |
| title             | Concise description of the vulnerability |
| description       | Detailed explanation with context        |
| severity          | HIGH or MEDIUM                           |
| recommendation    | Suggested fix or mitigation              |
| code_references   | List of affected code locations          |
