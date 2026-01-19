from typing import List, Optional
from pydantic import BaseModel
from enum import Enum

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
    # Category is optional and can be set as attribute
    category: Optional[str] = None

class FindingInput(BaseModel):
    """Model representing an input security finding submission"""
    task_id: str
    findings: List[Finding]
