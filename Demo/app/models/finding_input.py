from typing import List
from pydantic import BaseModel
from enum import Enum

# Only High and Medium risks should be submitted.
class Severity(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"

class FindingInput(BaseModel):
    """Model representing an input security finding submission"""
    project_id: str
    reported_by_agent: str
    finding_id: str
    title: str
    description: str
    severity: Severity
    recommendation: str
    code_references: List[str] 