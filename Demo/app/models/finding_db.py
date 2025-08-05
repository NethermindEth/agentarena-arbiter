from typing import Optional, Dict, Any
from beanie import Document
from pydantic import Field
from datetime import datetime, timezone
from enum import Enum
from app.models.finding_input import Finding

class Status(str, Enum):
    PENDING = "pending"
    ALREADY_REPORTED = "already_reported"
    SIMILAR_VALID = "similar_valid"
    UNIQUE_VALID = "unique_valid"
    BEST_VALID = "best_valid"
    DISPUTED = "disputed"

class EvaluatedSeverity(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"

class FindingDB(Document, Finding):
    """
    Model representing a processed security finding stored in the database.
    Extends the Finding model with additional system-managed fields.
    """
    # Additional required fields
    agent_id: str
    
    # System-added fields
    status: Status = Status.PENDING  # Default status
    deduplication_comment: Optional[str] = None  # Comment from deduplication
    evaluation_comment: Optional[str] = None  # Comment from evaluation
    evaluated_severity: Optional[EvaluatedSeverity] = None  # Severity after evaluation
    duplicateOf: Optional[str] = None  # ID of the original finding
    
    # Timestamps
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    
    @property
    def str_id(self) -> str:
        """
        Returns the string representation of the id for use as dictionary keys.
        This avoids the need for explicit str(f.id) conversions throughout the codebase.
        """
        return str(self.id)
    
    def dump(self) -> Dict[str, Any]:
        """
        Return a subset of fields for API responses.
        
        Returns:
            Dictionary containing only title, description, severity, file_paths, and duplicateOf
        """
        return {
            "id": str(self.id),
            "title": self.title,
            "description": self.description,
            "severity": self.severity,
            "file_paths": self.file_paths,
            "duplicateOf": self.duplicateOf
        }
