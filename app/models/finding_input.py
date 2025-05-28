from typing import List, Optional
from pydantic import BaseModel
from enum import Enum

# Only High and Medium risks should be submitted.
class Severity(str, Enum):
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"
    INFO = "Info"

class Finding(BaseModel):
    """Model representing a single finding"""
    title: str
    description: str
    severity: Severity
    file_paths: List[str]

class FindingInput(BaseModel):
    """Model representing an input security finding submission"""
    task_id: str
    findings: List[Finding] 