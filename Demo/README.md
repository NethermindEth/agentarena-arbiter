
## Overview

The validation pipeline processes security findings through three main stages:

1. **Deduplication**: Removes duplicate findings using hierarchical batch processing grouped by contract
2. **Initial Validation**: Validates findings one-by-one using a 3-step process (Technical → Contextual → Legitimacy) with schema guided reasoning. 
3. **Trusted Entity Analysis**: Categorizes and validates findings related to privileged actors (Admins, Owners, DAO, Keepers, Oracles) using a tri-cameral ensemble system

## Prerequisites

### Required Files

You need three types of files for each project you want to test:

1. **Findings JSON File**: Contains all security findings with metadata
   - Format: `{project}_findings.json` (e.g., `lido_findings.json`, `uniswap_findings.json`)
   - Location: Parent directory of `agentarena-arbiter` 
   - Required fields: `title`, `description`, `severity`, `file_paths`, `reviewer_rating` (optional, for ground truth)

2. **Information JSON File**: Contains project metadata, documentation, and Q&A responses
   - Format: `{project}_information.json` (e.g., `lido_information.json`, `uniswap_information.json`)
   - Location: Parent directory of `agentarena-arbiter`   
   - Required fields: `description`, `selectedFiles`, `additionalDocs`, `qaResponses`, `selectedDocs`

3. **Repository Directory**: The actual smart contract codebase
   - Format: `{project}_analysis/{repo_name}/` (e.g., `lido_analysis/lido-earn/`, `Uniswap_analysis/uniswapx/`)
   - Location: Parent directory of `agentarena-arbiter` 
   - Should contain all contract files referenced in `selectedFiles` from the information JSON

### File Structure

```
Parent-Directory/
├── agentarena-arbiter/
│   └── Demo/
│       ├── test_with_metrics.py  # Main test script
│       ├── app/                   # Application code
│       └── README.md              # This file
├── lido_findings.json             # Findings for Lido project
├── lido_information.json          # Metadata for Lido project
├── lido_analysis/
│   └── lido-earn/                 # Lido repository
├── uniswap_findings.json          # Findings for Uniswap project
├── uniswap_information.json       # Metadata for Uniswap project
└── Uniswap_analysis/
    └── uniswapx/                  # Uniswap repository
```

## Setup Instructions

### 1. Install Dependencies

```bash
cd agentarena-arbiter/Demo

# Create virtual environment (if not already created)
python3 -m venv venv

# Activate virtual environment
source venv/bin/activate  # On Linux/Mac
# OR
venv\Scripts\activate  # On Windows

# Install requirements
pip install -r requirements.txt
```

### 2. Configure API Keys

Create a `.env` file in the `Demo/` directory (copy from `.env.example`):

```bash
cp .env.example .env
```

Edit `.env` and add your API keys:

```env
# MongoDB Configuration
MONGODB_URL=mongodb://localhost:27017

# OpenAI Configuration (REQUIRED for validation)
OPENAI_API_KEY=your_openai_api_key_here
OPENAI_VALIDATION_MODEL=o3-2025-04-16

# Claude Configuration (REQUIRED for trusted entity analysis)
CLAUDE_API_KEY=your_claude_api_key_here
CLAUDE_MODEL=claude-sonnet-4-20250514

# Gemini Configuration (REQUIRED for deduplication)
GEMINI_API_KEY=your_gemini_api_key_here
GEMINI_MODEL=gemini-2.5-pro

# Backend API Configuration (optional, for production)
BACKEND_API_KEY=your_backend_api_key_here
BACKEND_FINDINGS_ENDPOINT=http://localhost:8000/api/findings
BACKEND_SUBMISSIONS_ENDPOINT=http://localhost:8000/api/submissions
BACKEND_TASK_REPOSITORY_ENDPOINT=http://localhost:8000/api/tasks

# Application Settings
DEBUG=false
LOG_LEVEL=INFO
MAX_FINDINGS_PER_SUBMISSION=20
```

**Required API Keys:**
- **OpenAI API Key**
- **Claude API Key**

### 3. Prepare Project Files

For each project you want to test:

1. **Place findings JSON** in the root directory:
   - `lido_findings.json` for Lido
   - `uniswap_findings.json` for Uniswap

2. **Place information JSON** in the root directory:
   - `lido_information.json` for Lido
   - `uniswap_information.json` for Uniswap

3. **Place repository** in the appropriate directory:
   - `lido_analysis/lido-earn/` for Lido
   - `Uniswap_analysis/uniswapx/` for Uniswap

4. **Update `get_project_paths()` function** in `test_with_metrics.py` if your project structure differs:
   ```python
   def get_project_paths(project: str, repo_root: Path) -> tuple[Path, Path, Path]:
       if project.lower() == "your_project":
           return (
               repo_root / "your_project_findings.json",
               repo_root / "your_project_information.json",
               repo_root / "your_project_analysis" / "repo_name"
           )
   ```

## Running the Pipeline

### Basic Usage

```bash
cd agentarena-arbiter/Demo

# Activate virtual environment
source venv/bin/activate

# Run for Lido project (default)
python test_with_metrics.py --project lido

# Run for Uniswap project
python test_with_metrics.py --project uniswap

# Show only final confusion matrix (skip initial validation metrics)
python test_with_metrics.py --project lido --final-only
```

### Command Line Arguments

- `--project`: Project to test (`lido` or `uniswap`), defaults to `lido`
- `--final-only`: Only show the final confusion matrix after trusted entity analysis (skip initial validation metrics)



