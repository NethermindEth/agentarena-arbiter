"""Tri-Cameral Ensemble System for Trusted Entity Finding Validation.

This module implements a three-agent ensemble system with different biases
and perspectives to validate trusted entity findings.
"""

from typing import List, Dict, Any, Optional, Tuple
from pydantic import BaseModel, Field
from app.models.finding_db import FindingDB
from app.core.claude_model import create_claude_model
from app.core.openai_model import send_prompt_to_openai_async
from app.core.prompt_utils import build_context_section
from app.types import TaskCache
from app.config import config


class AgentValidationResult(BaseModel):
    """Result from a single agent."""
    finding_id: str = Field(description="ID/title of the finding")
    is_valid: bool = Field(description="Whether the finding is valid according to this agent")
    reasoning: str = Field(description="Agent's reasoning for the decision")


class TriCameralResult(BaseModel):
    """Final result from tri-cameral ensemble."""
    finding_id: str = Field(description="ID/title of the finding")
    agent_lawyer: AgentValidationResult = Field(description="Result from Agent A (Lawyer)")
    agent_mathematician: AgentValidationResult = Field(description="Result from Agent B (Mathematician)")
    agent_safety: AgentValidationResult = Field(description="Result from Agent C (Safety Engineer)")
    score: float = Field(description="Weighted score from ensemble")
    final_verdict: str = Field(description="Final verdict: 'VALID', 'LIKELY_VALID', or 'INVALID'")
    reasoning: str = Field(description="Explanation of the final decision")


class TriCameralEnsemble:
    """Tri-Cameral Ensemble System for validating trusted entity findings."""
    
    def __init__(self):
        """Initialize the ensemble with three agents."""
        # Agent A: The Lawyer (Claude Opus 4.5)
        self.agent_lawyer_model = create_claude_model(
            model_name="claude-opus-4-5-20251101",
            temperature=0.0,
            max_tokens=8000
        )
        
        # Agent C: The Safety Engineer (Claude Opus 4.5)
        self.agent_safety_model = create_claude_model(
            model_name="claude-opus-4-5-20251101",
            temperature=0.0,
            max_tokens=8000
        )
        
        # Agent B: The Mathematician (O3 with high reasoning effort)
        # Will use OpenAI model directly
    
    async def validate_finding(
        self,
        finding: FindingDB,
        task_cache: TaskCache,
        summary: str,
        dev_doc: str,
    ) -> TriCameralResult:
        """
        Validate a single finding using the tri-cameral ensemble.
        
        Args:
            finding: The finding to validate
            task_cache: Task context
            summary: Project summary
            dev_doc: Developer documentation
            
        Returns:
            TriCameralResult with all agent results and final verdict
        """
        # Run all three agents in parallel
        lawyer_result, mathematician_result, safety_result = await self._run_ensemble_parallel(
            finding, task_cache, summary, dev_doc
        )
        
        # Calculate weighted score
        score = self._calculate_score(lawyer_result, mathematician_result, safety_result)
        
        # Determine final verdict
        final_verdict, reasoning = self._determine_verdict(score, lawyer_result, mathematician_result, safety_result)
        
        return TriCameralResult(
            finding_id=finding.title,
            agent_lawyer=lawyer_result,
            agent_mathematician=mathematician_result,
            agent_safety=safety_result,
            score=score,
            final_verdict=final_verdict,
            reasoning=reasoning
        )
    
    async def _run_ensemble_parallel(
        self,
        finding: FindingDB,
        task_cache: TaskCache,
        summary: str,
        dev_doc: str,
    ) -> Tuple[AgentValidationResult, AgentValidationResult, AgentValidationResult]:
        """Run all three agents in parallel."""
        import asyncio
        
        # Create prompts for each agent
        lawyer_prompt = self._build_lawyer_prompt(finding, task_cache, summary, dev_doc)
        mathematician_prompt = self._build_mathematician_prompt(finding, task_cache, summary, dev_doc)
        safety_prompt = self._build_safety_prompt(finding, task_cache, summary, dev_doc)
        
        # Run all agents in parallel
        lawyer_task = self._run_lawyer_agent(lawyer_prompt, finding.title)
        mathematician_task = self._run_mathematician_agent(mathematician_prompt, finding.title)
        safety_task = self._run_safety_agent(safety_prompt, finding.title)
        
        lawyer_result, mathematician_result, safety_result = await asyncio.gather(
            lawyer_task,
            mathematician_task,
            safety_task
        )
        
        return lawyer_result, mathematician_result, safety_result
    
    async def _run_lawyer_agent(self, prompt: str, finding_id: str) -> AgentValidationResult:
        """Run Agent A: The Lawyer."""
        structured_model = self.agent_lawyer_model.with_structured_output(AgentValidationResult)
        try:
            result = await structured_model.ainvoke(prompt)
            return result
        except Exception as e:
            # Fallback: return conservative rejection
            return AgentValidationResult(
                finding_id=finding_id,
                is_valid=False,
                reasoning=f"Error in lawyer agent: {str(e)}. Defaulting to rejection (conservative)."
            )
    
    async def _run_mathematician_agent(self, prompt: str, finding_id: str) -> AgentValidationResult:
        """Run Agent B: The Mathematician (O3 with high reasoning)."""
        try:
            result = await send_prompt_to_openai_async(
                model_type=config.openai_validation_model,
                messages=prompt,
                response_model=AgentValidationResult,
                thinking=True,  # High reasoning effort
                web_search=False
            )
            if result is None:
                # Fallback: return acceptance (mathematician is biased to accept)
                return AgentValidationResult(
                    finding_id=finding_id,
                    is_valid=True,
                    reasoning="Error in mathematician agent. Defaulting to acceptance (biased to accept)."
                )
            return result
        except Exception as e:
            # Fallback: return acceptance (mathematician is biased to accept)
            return AgentValidationResult(
                finding_id=finding_id,
                is_valid=True,
                reasoning=f"Error in mathematician agent: {str(e)}. Defaulting to acceptance (biased to accept)."
            )
    
    async def _run_safety_agent(self, prompt: str, finding_id: str) -> AgentValidationResult:
        """Run Agent C: The Safety Engineer."""
        structured_model = self.agent_safety_model.with_structured_output(AgentValidationResult)
        try:
            result = await structured_model.ainvoke(prompt)
            return result
        except Exception as e:
            # Fallback: return acceptance (safety is biased to accept)
            return AgentValidationResult(
                finding_id=finding_id,
                is_valid=True,
                reasoning=f"Error in safety agent: {str(e)}. Defaulting to acceptance (biased to safety)."
            )
    
    def _build_lawyer_prompt(
        self,
        finding: FindingDB,
        task_cache: TaskCache,
        summary: str,
        dev_doc: str,
    ) -> str:
        """Build prompt for Agent A: The Audit Scope Lawyer."""
        context_section = build_context_section(task_cache)
        
        return f"""You are a Strict Audit Judge for Code4rena/Sherlock. Your job is to enforce the 'Admin Competence Assumption' while distinguishing between Supreme and Operational roles.

## YOUR ROLE: The Audit Scope Lawyer
**Focus:** Trust Model, Documentation, & Role Hierarchy
**Behavior:** Conservative. Strict adherence to the README. Distinguishes "God Mode" from "Job Mode".

## PRIMARY DIRECTIVE
Analyze the **Entity Trust Level** involved in the finding.
1. **Supreme Roles (Owner, Admin, DAO, Timelock):** Assume they are competent and honest.
   - If finding claims Supreme Role *chooses* to destroy protocol → **REJECT**.
2. **Operational Roles (Keeper, Cosigner, Oracle, Validator, Resolver, Manager):** These are "Semi-Trusted".
   - They are trusted *only* to perform their specific job.
   - If finding claims Operational Role can destroy funds, brick the system, or bypass constraints → **ACCEPT** (Privilege Escalation).

## REJECTION CRITERIA
- Reject finding if it relies on a **Supreme Role** acting maliciously or against their economic interest.
- Reject finding if it describes intended behavior explicitly documented in the README.

## ACCEPTANCE CRITERIA
- Accept finding if a **Supreme Role** attempts to do something good (e.g., upgrade, recovery) but the code fails them.
- Accept finding if an **Operational Role** (Semi-Trusted) has excessive power (e.g., "Keeper can drain vault" or "Cosigner can authorize infinite spend").

## PROJECT CONTEXT
**Summary:**
{summary}

**Developer Documentation:**
{dev_doc}

## SMART CONTRACT CODE
{context_section}

## FINDING TO EVALUATE
**Title:** {finding.title}
**Description:** {finding.description}
**Severity:** {finding.severity}
**File Paths:** {', '.join(finding.file_paths) if finding.file_paths else 'N/A'}

## YOUR TASK
Evaluate this finding from the perspective of an audit scope lawyer.
1. Identify the Actor. Is it Supreme (Owner) or Operational (Keeper/Cosigner)?
2. Is the Actor acting within their "Job Description" or are they breaking the system?
3. Does the code grant "God Mode" powers to a "Job Mode" role?

Return your evaluation with:
- is_valid: true if you accept, false if you reject
- reasoning: Brief explanation citing the specific role's trust level (2-3 sentences)
"""
    
    def _build_mathematician_prompt(
        self,
        finding: FindingDB,
        task_cache: TaskCache,
        summary: str,
        dev_doc: str,
    ) -> str:
        """Build prompt for Agent B: The State Machine Mechanic."""
        context_section = build_context_section(task_cache)
        
        return f"""You are a Formal Verification Engineer. You DO NOT CARE about 'who' calls a function. You only care about 'State Consistency' and 'Data Integrity'.

## YOUR ROLE: The State Machine Mechanic
**Focus:** Invariants, Mathematical Correctness & Data Consistency
**Behavior:** Blind to permissions. Treats onlyOwner functions exactly the same as public functions.

## PRIMARY DIRECTIVE
Trace the variables and data flow.
1. **Mathematical Truth:** Does the transaction violate conservation of funds? Does it break a snapshot?
2. **Data Integrity (The "Two-Source" Rule):** Does the code accept data from two different sources (e.g., User Signature vs. External Resolver) without explicitly checking they match?

## REJECTION CRITERIA
- Reject findings where the math is correct and data is consistent, even if the outcome is "unfair" by design.

## ACCEPTANCE CRITERIA
- **State Violation:** Assets != Liabilities, Supply changes during a freeze.
- **Integrity Violation:** Redundant data sources are not checked for equality.
- **Order of Operations:** Trusted action occurs *after* a snapshot is frozen.
- **The "Leakage" Rule (Locked Funds):**
  - Does the math (e.g., floor division) leave "Dust" or remainder assets in the contract?
  - Is there a way to retrieve this dust (e.g., a `sweep` function)?
  - **Verdict:** If Dust is created AND cannot be retrieved, ACCEPT (Funds are permanently locked).

## CRITICAL INSTRUCTION
**IGNORE THE WORD 'ADMIN'.** If the math breaks or data is mismatched, it is a bug. Treat all functions as if they are public.

## PROJECT CONTEXT
**Summary:**
{summary}

**Developer Documentation:**
{dev_doc}

## SMART CONTRACT CODE
{context_section}

## FINDING TO EVALUATE
**Title:** {finding.title}
**Description:** {finding.description}
**Severity:** {finding.severity}
**File Paths:** {', '.join(finding.file_paths) if finding.file_paths else 'N/A'}

## YOUR TASK
Evaluate this finding from the perspective of a formal verification engineer.
1. Does this violate state invariants?
2. Does the code fail to enforce equality between two redundant data sources?
3. Is the math broken?

Return your evaluation with:
- is_valid: true if you accept (broken math/integrity), false if you reject (sound logic)
- reasoning: Brief explanation focusing on state consistency/data flow (2-3 sentences)
"""
    
    def _build_safety_prompt(
        self,
        finding: FindingDB,
        task_cache: TaskCache,
        summary: str,
        dev_doc: str,
    ) -> str:
        """Build prompt for Agent C: The Safety Engineer."""
        context_section = build_context_section(task_cache)
        
        return f"""You are a Chaos Engineer. Your job is to predict 'Murphy's Law' (Anything that can go wrong, will) and enforce 'Defense in Depth'.

## YOUR ROLE: The Safety Engineer
**Focus:** Foot-guns, Operational Risks & Fail-Safes
**Behavior:** Pragmatic. Assumes servers will crash and keys will be stolen.

## PRIMARY DIRECTIVE
Identify code paths that are 'brittle' or lack 'Defense in Depth'.
**Assumption:** Off-chain servers (Cosigners/Oracles) WILL be compromised. Is there an on-chain safety net?

## REJECTION CRITERIA
- Reject 'Theoretical' attacks that cost infinite money or require 51% attacks.
- Reject findings that are purely 'User Error' where the user gets exactly what they asked for (UNLESS the default is dangerous).

## ACCEPTANCE CRITERIA
- **Defense in Depth:** Accept if an off-chain failure (hacked cosigner/oracle) leads to UNLIMITED loss.
- **Dangerous Defaults:** Accept if a user inputting `0` results in a total loss.
- **The "Red Button" Rule (Emergency Fragility):**
  - Is the function related to `Recovery`, `Emergency`, `Pause`, or `Shutdown`?
  - Can an attacker block this function (DoS) even temporarily or cheaply (e.g., by sending 1 wei)?
  - **Verdict:** If YES, ACCEPT. (Fragility in emergencies is Critical).
- **Fragility:** Accept if a simple typo causes PERMANENT Denial of Service.

## KEY QUESTION
1. 'If the Admin is trying to save the protocol (Emergency Mode), can an attacker stop them?'
2. 'If the server is hacked, does the code stop the bleeding?'

## PROJECT CONTEXT
**Summary:**
{summary}

**Developer Documentation:**
{dev_doc}

## SMART CONTRACT CODE
{context_section}

## FINDING TO EVALUATE
**Title:** {finding.title}
**Description:** {finding.description}
**Severity:** {finding.severity}
**File Paths:** {', '.join(finding.file_paths) if finding.file_paths else 'N/A'}

## YOUR TASK
Evaluate this finding from the perspective of a chaos engineer.
1. Is there a missing on-chain guardrail for an off-chain dependency? → ACCEPT
2. Does a zero-input cause a zero-payout theft? → ACCEPT
3. Is there a sanity check missing? → ACCEPT

Return your evaluation with:
- is_valid: true if you accept (dangerous lack of safety rails), false if you reject (theoretical or protected)
- reasoning: Brief explanation focusing on operational risks and missing guardrails (2-3 sentences)
"""
    
    def _calculate_score(
        self,
        lawyer_result: AgentValidationResult,
        mathematician_result: AgentValidationResult,
        safety_result: AgentValidationResult,
    ) -> float:
        """
        Calculate weighted score based on agent votes.
        
        Scoring:
        - Agent B (Mathematician) Says Valid: +3.0 Points
        - Agent C (Safety) Says Valid: +1.5 Points
        - Agent A (Lawyer) Says Valid: +1.0 Point
        """
        score = 0.0
        
        if mathematician_result.is_valid:
            score += 3.0
        if safety_result.is_valid:
            score += 1.5
        if lawyer_result.is_valid:
            score += 1.0
        
        return score
    
    def _determine_verdict(
        self,
        score: float,
        lawyer_result: AgentValidationResult,
        mathematician_result: AgentValidationResult,
        safety_result: AgentValidationResult,
    ) -> Tuple[str, str]:
        """
        Determine final verdict based on score.
        
        Thresholds:
        - Score >= 3: VALID BUG (Math is broken)
        - Score >= 1.5 AND < 3: LIKELY VALID (Dangerous lack of safety rails)
        - Score < 1.5: INVALID / TRUSTED ISSUE
        """
        if score >= 3.0:
            return "VALID", f"Score {score:.1f}: Math/logic is broken (Mathematician validated). This is a valid bug regardless of trust assumptions."
        elif score >= 1.5:
            return "LIKELY_VALID", f"Score {score:.1f}: Dangerous lack of safety rails (Safety Engineer validated). Operational risk exists."
        else:
            return "INVALID", f"Score {score:.1f}: Insufficient evidence. This appears to be a trusted entity issue without logical/mathematical flaws."

