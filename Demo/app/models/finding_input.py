from typing import List
from pydantic import BaseModel

class FindingInput(BaseModel):
    """Model representing an input security finding submission"""
    project_id: str
    reported_by_agent: str
    finding_id: str
    title: str
    description: str
    severity: str
    recommendation: str
    code_references: List[str] 