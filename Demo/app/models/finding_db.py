from typing import List, Optional
from pydantic import BaseModel, Field
from datetime import datetime
from enum import Enum
from app.models.finding_input import FindingInput

class Status(str, Enum):
    PENDING = "pending"
    ALREADY_REPORTED = "already_reported"
    SIMILAR_VALID = "similar_valid"
    UNIQUE_VALID = "unique_valid"
    DISPUTED = "disputed"

class EvaluatedSeverity(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"

class FindingDB(FindingInput):
    """
    Model representing a processed security finding stored in the database.
    Extends the input model with additional system-managed fields.
    """
    # Submission batch identifier (sequential counter per agent)
    submission_id: int = 1  # Default to 1, will be assigned by the system

    # System-added fields
    status: Status = Status.PENDING  # Default status
    category: Optional[str] = None  # Security issue category (e.g., "SQL Injection", "XSS")
    category_id: Optional[str] = None  # Unique identifier for the category group
    evaluated_severity: Optional[EvaluatedSeverity] = None  # Severity after evaluation
    evaluation_comment: Optional[str] = None  # Arbiter comments on the finding
    
    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow) 