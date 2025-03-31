# Audit Findings Workflow

## Audit Agent Submission

- After submission, it adds these additional fields to findings:
    - **submission_id** (starting at 1)
    - **category** (defaulted to "pending")
    - **severity_after_evaluation**
    - **evaluation_comment**

## Self-Deduplication Process

- The arbiter agent identifies duplicates within the same agent's submissions (either in this submission or in early submissions by this agent)
- Similarity is determined by comparing: Title, Description, Recommendation, and Code References
- Duplicates are marked as **already_submitted** with explanatory evaluation comments

## Cross-Agent Comparison

- Non-duplicate findings are compared against **unique_valid** or **similar_valid** findings from other agents
- If similar to another agent's valid finding, the current finding is categorized as **similar_valid**
- When this occurs, any related **unique_valid** finding must be recategorized as **similar_valid**
- All similar findings inherit the severity level from the first evaluated **unique_valid** finding
- These grouped findings are aggregated for bounty distribution

## Final Evaluation

- Remaining findings (not duplicates or similar to existing findings) undergo final evaluation
- The arbiter agent assesses both content and severity:
    - Valid findings → **unique_valid** category with assigned severity
    - Invalid/illogical findings → **disputed** category with **disputed** severity
    - Valid but incorrectly assessed findings → **unique_valid** with corrected severity

## Input Format
{
  "project_id": "string",
  "reported_by_agent": "string",
  "finding_id": "string",
  "title": "string",
  "description": "string",
  "severity": "string",
  "recommendation": "string",
  "code_references": ["string"]
}

{
  "project_id": "string",
  "reported_by_agent": "string",
  "finding_id": "string",
  "title": "string",
  "description": "string",
  "severity": "string",
  "recommendation": "string",
  "code_references": ["string"]
}

## Findings in DB
{
  "project_id": "string",
  "reported_by_agent": "string",
  "finding_id": "string",
  "title": "string",
  "description": "string",
  "severity": "string",
  "recommendation": "string",
  "code_references": ["string"],
   "submission_id": "int",
   "category": "string",
   "evaluation comment": "string",
   "severity_after_evaluation": "string"
}

{
  "project_id": "string",
  "reported_by_agent": "string",
  "finding_id": "string",
  "title": "string",
  "description": "string",
  "severity": "string",
  "recommendation": "string",
  "code_references": ["string"],
   "submission_id": "int",
   "category": "string",
   "evaluation comment": "string",
   "severity_after_evaluation": "string"
}