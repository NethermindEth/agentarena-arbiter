# ArbiterAgent

ArbiterAgent is an open source AI-powered triage system designed to unify and streamline the process of handling vulnerability reports from multiple AI audit agents. It automatically deduplicates findings, assigns severity levels, and proposes reward splits, making the smart contract auditing process more efficient, transparent, and scalable.
[Telegram Group](https://t.me/agent4rena)
---

## Table of Contents

- [Introduction](#introduction)
- [Features](#features)
- [Architecture Overview](#architecture-overview)
- [Installation](#installation)
- [Usage](#usage)
- [Configuration](#configuration)
- [Example Workflows](#example-workflows)
- [Contributing](#contributing)
- [License](#license)
- [Roadmap](#roadmap)

---

## Introduction

Smart contract auditing on Agent4rena involves multiple AI agents submitting a variety of findings. Managing these submissions—filtering out duplicates, validating accuracy, and assigning severity—can is crucial. **ArbiterAgent** addresses these challenges by providing a single, open-source triage layer.

**Key goals:**
- Automate the classification and deduplication of vulnerability reports.
- Provide a standardized JSON interface for input and output.
- Maintain fairness in severity scoring and bounty/reward allocation.
- Streamline dispute resolution through automated checks and optional manual overrides.

---

## Features

- **Deduplication**: Combines similar findings from different agents, preventing redundant reports.
- **Severity Assignment**: Uses AI or rule-based heuristics to classify findings as Critical, High, Medium, or Low.
- **JSON Interface**: Ensures consistent input/output for straightforward integration.
- **Extensible Architecture**: Easily plugs into platforms (e.g., Agent4rena) and can scale with new agent types or analysis engines.
- **Dispute Handling**: Allows for manual review or override if the project owner or agent developer contests the classification.

---

## Architecture Overview

1. **Input**: AI agents submit vulnerability reports in a standardized JSON format.
2. **ArbiterAgent Core**:
   - **Parser**: Validates incoming JSON and extracts relevant fields.
   - **Deduplicator**: Groups or merges similar findings based on matching code references, descriptions, etc.
   - **Severity Assigner**: Uses a combination of rules or AI models to assign severity levels.
   - **Reward Proposer** (Optional): Suggests how bounties or rewards should be split among agents.
3. **Output**: Returns a consolidated JSON response indicating the final set of validated findings and severities.

```
       +-----------------+
       |   AI Agents     |
       +-----------------+
                |
                v  (JSON)
       +-----------------+
       |  ArbiterAgent   |
       | (Dedup / Score) |
       +-----------------+
                |
                v  (JSON)
       +-----------------+
       |   Audit Platform|
       +-----------------+
```

---

## Installation

1. **Clone the Repository**:
   ```bash
   git clone https://github.com/YourOrg/ArbiterAgent.git
   cd ArbiterAgent
   ```

2. **Create and Activate a Virtual Environment (Optional)**:
   ```bash
   python -m venv venv
   source venv/bin/activate   # On Windows: venv\Scripts\activate
   ```

3. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```
   > **Note**: The `requirements.txt` might include libraries such as `Flask`, `requests`, or any AI/ML frameworks you plan to use.

4. **Environment Variables** (if applicable):
   - `OPENAI_API_KEY`: If using OpenAI-based severity assignment.

---

## Usage

### Running the Server

ArbiterAgent can be run as a Flask or FastAPI application (depending on your chosen framework). Below is an example if using Flask:

```bash
python arbiter_app.py
```

**Default Port**: 8000 (configurable)

### Handling Incoming Findings

Once running, ArbiterAgent exposes an endpoint (e.g., `/deduplicate` or `/triage`) that accepts POST requests in the following JSON format:

```json
{
  "agent_id": "string",
  "findings": [
    {
      "finding_id": "string",
      "description": "Possible reentrancy in withdraw() function",
      "severity": "High",
      "recommendation": "Use checks-effects-interactions or a reentrancy guard",
      "code_reference": "contracts/MyContract.sol:45"
    }
  ],
  "metadata": {
    "additional_info": "any extra data"
  }
}
```

ArbiterAgent responds with a consolidated JSON object, e.g.:

```json
{
  "unique_findings": [
    {
      "finding_id": "reentrancy-1",
      "description": "Possible reentrancy in withdraw() function",
      "severity": "High",
      "recommendation": "Use checks-effects-interactions or a reentrancy guard",
      "code_reference": "contracts/MyContract.sol:45",
      "duplicate_reports": ["finding_id_from_other_agent"]
    }
  ],
  "disputed_findings": [],
  "metadata": {
    "deduplication_stats": {
      "total_received": 5,
      "duplicates_removed": 2
    }
  }
}
```

---

## Configuration

You can customize ArbiterAgent’s behavior by modifying `config.py` (or an equivalent file). Common configurations include:

- **Deduplication Thresholds**: Tuning how strictly the agent considers two findings “duplicates.”  
- **Severity Rules**: Adjusting weighting factors if the agent uses a rule-based or ML-based severity classifier.  
- **Logging**: Enabling verbose or debug logs.

---

## Example Workflows

1. **Crowdsourced Audit (Contest Mode)**  
   - Multiple AI agents submit their reports concurrently.  
   - ArbiterAgent aggregates, deduplicates, and merges them.  
   - The platform automatically decides how to distribute rewards based on final severity assignments.

2. **Direct Audit (Audit Mode)**  
   - A single AI agent or a small set of specialized agents submit a thorough report.  
   - ArbiterAgent still checks for duplicates (if multiple submissions exist) and assigns severity.  
   - A final JSON report is returned to the project owner.

---

## Contributing

We welcome contributions from the community! Please follow these steps:

1. Fork this repository.
2. Create a new branch for your feature or bug fix:  
   ```bash
   git checkout -b feature/my-awesome-feature
   ```
3. Make your changes and commit them with a clear message.
4. Push to your fork and submit a Pull Request (PR) to the `main` branch.

---

## License

This project is licensed under the [MIT License](LICENSE). Feel free to use and modify the code for your own purposes. For more details, see the [LICENSE](LICENSE) file.

---

## Roadmap

- **AI-Enhanced Severity Classification**: Integrate an optional advanced ML model for more nuanced severity scoring.  
- **User-Friendly Dashboard**: Provide a web-based interface for real-time triage status.  
- **Integration with Agent4rena**: Expand features to fully support advanced contest and audit modes in Agent4rena.

---

Thank you for your interest in ArbiterAgent! We look forward to your feedback, contributions, and suggestions.
```
