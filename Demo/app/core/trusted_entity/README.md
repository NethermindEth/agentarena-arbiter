# Trusted Entity Analysis Module

This module provides advanced analysis for security findings related to trusted entities (Admins, Owners, DAO, Emergency Roles, Keepers, Oracles).

## Overview

The trusted entity analysis pipeline consists of two main stages:

1. **Categorization**: Identifies which validated findings are related to trusted entities
2. **Tri-Cameral Ensemble Validation**: Uses three specialized agents with different biases to validate trusted entity findings

## Architecture

### Categorization Stage

Uses **Claude Sonnet 4** to categorize findings into:
- `malicious_abuse`: Rug pulls, admin can drain funds
- `competence_error`: Admin might set bad values, misconfiguration
- `privileged_logic_error`: Bugs in admin-only functions
- `not_trusted_entity`: Not related to trusted entities

### Tri-Cameral Ensemble

Three agents with different perspectives and biases:

1. **Agent A: The Audit Scope Lawyer** (Claude Opus 4.5)
   - **Focus**: Trust Model & Documentation
   - **Bias**: Conservative, rejects malicious admin assumptions
   - **Weight**: +1.0 point if valid

2. **Agent B: The State Machine Mechanic** (O3 with high reasoning)
   - **Focus**: Invariants & Mathematical Correctness
   - **Bias**: Blind to permissions, focuses on math/logic
   - **Weight**: +3.0 points if valid (strongest signal)

3. **Agent C: The Safety Engineer** (Claude Opus 4.5)
   - **Focus**: Foot-guns & Operational Risks
   - **Bias**: Pragmatic, looks for fragility
   - **Weight**: +1.5 points if valid

### Scoring System

The ensemble uses a weighted scoring system:

```
score = 0
if agent_mathematician.is_valid: score += 3.0
if agent_safety.is_valid: score += 1.5
if agent_lawyer.is_valid: score += 1.0
```

**Verdict Thresholds:**
- `Score >= 3.0`: **VALID** - Math/logic is broken
- `Score >= 1.5 AND < 3.0`: **LIKELY_VALID** - Dangerous lack of safety rails
- `Score < 1.5`: **INVALID** - Trusted entity issue without logical flaws

## Usage

### Basic Usage

```python
from app.core.trusted_entity.orchestrator import run_trusted_entity_analysis
from app.models.finding_db import FindingDB
from app.types import TaskCache

# Load your validated findings (findings that passed initial validation)
validated_findings: List[FindingDB] = [...]  # Your validated findings
task_cache: TaskCache = ...  # Your task cache
summary: str = ...  # Project summary
dev_doc: str = ...  # Developer documentation

# Run analysis
result = await run_trusted_entity_analysis(
    validated_findings=validated_findings,
    task_cache=task_cache,
    summary=summary,
    dev_doc=dev_doc
)

# Access results
print(f"Total findings: {result.stats['total_findings']}")
print(f"Trusted entity findings: {result.stats['trusted_entity_count']}")
print(f"Valid: {result.stats['valid_count']}")
print(f"Likely valid: {result.stats['likely_valid_count']}")
print(f"Invalid: {result.stats['invalid_count']}")

# Get final validated findings
final_findings = result.final_validated_findings

# Access tri-cameral results
for tri_result in result.tri_cameral_results:
    print(f"Finding: {tri_result.finding_id}")
    print(f"  Lawyer: {tri_result.agent_lawyer.is_valid}")
    print(f"  Mathematician: {tri_result.agent_mathematician.is_valid}")
    print(f"  Safety: {tri_result.agent_safety.is_valid}")
    print(f"  Score: {tri_result.score}")
    print(f"  Verdict: {tri_result.final_verdict}")
```

### Integration with Existing Pipeline

Add this after your validation step in `test_with_metrics.py`:

```python
# After validation
validated_findings = [
    f for f in findings
    if validation_results.get(f.id, {}).get('final_result', False)
]

# Run trusted entity analysis
from app.core.trusted_entity.orchestrator import run_trusted_entity_analysis

te_result = await run_trusted_entity_analysis(
    validated_findings=validated_findings,
    task_cache=task_cache,
    summary=summary,
    dev_doc=dev_doc
)

# Use final validated findings
final_findings = te_result.final_validated_findings
```

## Module Structure

```
trusted_entity/
├── __init__.py              # Module exports
├── categorization.py        # Categorization logic (Claude Sonnet 4)
├── tri_cameral.py          # Tri-cameral ensemble system
├── orchestrator.py         # Main orchestration pipeline
└── README.md               # This file
```

## Models Used

- **Categorization**: `claude-sonnet-4-20250514`
- **Agent A (Lawyer)**: `claude-opus-4-5-20251101`
- **Agent B (Mathematician)**: `o3-2025-04-16` (with `reasoning: {"effort": "high"}`)
- **Agent C (Safety)**: `claude-opus-4-5-20251101`

## Theory

The tri-cameral system is designed to capture "Uncorrelated Errors" by forcing the AI into three fundamentally different mental models:

1. **The Lawyer** catches "Intended Centralization" risks that are out of scope
2. **The Mathematician** detects logical/mathematical contradictions regardless of permissions
3. **The Safety Engineer** identifies operational risks and missing guardrails

By using different biases and perspectives, the system reduces the chance of all three agents making the same mistake, leading to more robust validation.

## Output Format

The `TrustedEntityAnalysisResult` contains:

- `stats`: Dictionary with counts and statistics
- `categorized_findings`: List of categorization results
- `tri_cameral_results`: List of tri-cameral validation results
- `final_validated_findings`: Final list of validated findings (includes non-trusted-entity findings that passed initial validation)

Use `result.to_dict()` to serialize the results to JSON.

