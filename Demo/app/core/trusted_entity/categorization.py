"""Categorization module for identifying Trusted Entity related findings."""

from typing import List, Dict, Any
from pydantic import BaseModel, Field
from app.models.finding_db import FindingDB
from app.core.openai_model import send_prompt_to_openai_async
from app.core.prompt_utils import build_context_section
from app.types import TaskCache
from app.config import config


class TrustedEntityCategory(BaseModel):
    """Result of trusted entity categorization."""
    finding_id: str = Field(description="ID/title of the finding")
    is_trusted_entity: bool = Field(description="Whether the finding is related to trusted entities")
    category_type: str = Field(
        description="Category type: 'malicious_abuse', 'competence_error', 'privileged_logic_error', or 'not_trusted_entity'"
    )
    reasoning: str = Field(description="Brief explanation of the categorization decision")


class CategorizationResult(BaseModel):
    """Batch categorization result."""
    results: List[TrustedEntityCategory] = Field(description="List of categorization results")


async def categorize_trusted_entity_findings(
    findings: List[FindingDB],
    task_cache: TaskCache,
) -> List[TrustedEntityCategory]:
    """
    Categorize findings to identify those related to Trusted Entities.
    
    Uses O3 with high reasoning effort for categorization (single important call).
    
    Args:
        findings: List of validated findings to categorize
        task_cache: Task context containing smart contract files and documentation
        
    Returns:
        List of categorization results
    """
    if not findings:
        return []
    
    # Build prompt
    prompt = _build_categorization_prompt(findings, task_cache)
    
    # Use O3 with high reasoning effort for this important categorization step
    result = await send_prompt_to_openai_async(
        model_type=config.openai_validation_model,
        messages=prompt,
        response_model=CategorizationResult,
        thinking=True,  # High reasoning effort for better classification
        web_search=False
    )
    
    if result is None:
        # Fallback: return empty results if categorization fails
        return []
    
    return result.results


def _build_categorization_prompt(findings: List[FindingDB], task_cache: TaskCache) -> str:
    """Build the categorization prompt for trusted entity findings."""
    
    context_section = build_context_section(task_cache)
    
    findings_text = "\n\n".join([
        f"**Finding {i+1}:**\n"
        f"ID: {finding.title}\n"
        f"Description: {finding.description}\n"
        f"Severity: {finding.severity}\n"
        f"File Paths: {', '.join(finding.file_paths) if finding.file_paths else 'N/A'}\n"
        for i, finding in enumerate(findings)
    ])
    
    return f"""You are an expert smart contract security auditor. Your goal is to categorize if a security finding is related to **Trusted Entities** (Admins, Owners, DAO, Emergency Roles, Keepers, Oracles).

This is a ROUTING step. You are not judging validity yet. You are deciding if the finding belongs in the "Trusted Entity" queue.

## Definition: What is a "Trusted Entity Finding"?
A finding belongs in this category if the **Root Cause** or the **Trigger** involves a privileged actor. This includes THREE sub-types:

1. **Malicious Abuse (Rug Pulls):**
   - Claims the Admin can steal funds, pause forever, or destroy the protocol.
   - *Keywords:* "Owner can drain," "Admin can censorship," "Centralization Risk."

2. **Competence/Config Errors (The "Clumsy Admin"):**
   - Claims the Admin might set a "bad value" (e.g., fee > 100%) that breaks the contract.
   - Claims the Admin might misconfigure an external dependency.

3. **Privileged Logic Errors (The "Harvest Fee" Case):**
   - **CRITICAL:** The finding describes a bug in a function restricted to Admins (e.g., `onlyOwner`, `activateRecovery`).
   - The Admin might not *want* to break it, but calling their specific function *causes* a break (e.g., "Calling activateRecovery disrupts the accounting").
   - *Rule:* If the vulnerable function has an access control modifier, it GOES HERE.

## What is NOT a Trusted Entity Finding?
- Exploits triggerable by a purely anonymous, unprivileged user (Public functions).
- Math errors that happen automatically without any Admin interaction.

## SMART CONTRACT CONTEXT
{context_section}

## FINDINGS TO CATEGORIZE
{findings_text}

## INSTRUCTIONS
For each finding:
1. Determine if it is related to Trusted Entities
2. If yes, classify it as one of: "malicious_abuse", "competence_error", or "privileged_logic_error"
3. If no, classify it as "not_trusted_entity"
4. Provide brief reasoning for your decision

Return a categorization result for each finding using the exact finding ID/title.
"""

