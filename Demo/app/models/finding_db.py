from typing import List, Optional
from pydantic import BaseModel, Field
from datetime import datetime
from enum import Enum
from app.models.finding_input import Finding, Severity

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

class FindingDB(Finding):
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
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow) 