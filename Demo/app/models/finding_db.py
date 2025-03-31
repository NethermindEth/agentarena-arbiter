from typing import List, Optional
from pydantic import BaseModel, Field
from datetime import datetime
from app.models.finding_input import FindingInput

class FindingDB(FindingInput):
    """
    Model representing a processed security finding stored in the database.
    Extends the input model with additional system-managed fields.
    """
    # Submission batch identifier (sequential counter per agent)
    submission_id: int = 1  # Default to 0, will be assigned by the system
    
    # System-added fields
    category: str = "pending"  # Default categorization status
    severity_after_evaluation: Optional[str] = None  # Updated severity after review
    evaluation_comment: Optional[str] = None  # Arbiter comments on the finding
    
    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow) 