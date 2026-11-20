from enum import Enum

from pydantic import BaseModel


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
    file_paths: list[str]
