from datetime import UTC, datetime
from enum import Enum
from typing import Any

from bson import ObjectId
from pydantic import ConfigDict, Field

from app.models.finding_input import Finding, Severity


class Status(str, Enum):
    PENDING = "pending"
    ALREADY_REPORTED = "already_reported"
    SIMILAR_VALID = "similar_valid"
    UNIQUE_VALID = "unique_valid"
    BEST_VALID = "best_valid"
    DISPUTED = "disputed"


class FindingDB(Finding):
    """
    Model representing a processed security finding stored in the database.
    Extends the Finding model with additional system-managed fields.
    """

    model_config = ConfigDict(
        arbitrary_types_allowed=True,  # Allow ObjectId type
        populate_by_name=True,  # Allow both 'id' and '_id' field names
    )

    # MongoDB document ID
    id: ObjectId | None = Field(default=None, alias="_id")

    # Additional required fields
    agent_id: str

    # System-added fields
    status: Status = Status.PENDING  # Default status
    duplicateOf: str | None = None  # ID of the original finding
    deduplication_comment: str | None = None  # Comment from deduplication
    evaluated_severity: Severity | None = None  # Severity after evaluation
    evaluation_comment: str | None = None  # Comment from evaluation

    # Timestamps
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def str_id(self) -> str:
        """
        Returns the string representation of the id for use as dictionary keys.
        This avoids the need for explicit str(f.id) conversions throughout the codebase.
        """
        return str(self.id)

    def dump(self) -> dict[str, Any]:
        """
        Return a subset of fields for API responses.

        Returns:
            Dictionary containing only title, description, severity, file_paths, and duplicateOf
        """

        if self.duplicateOf:
            return {
                "id": str(self.id),
                "title": self.title,
                "description": self.description,
                "severity": self.severity,
                "file_paths": self.file_paths,
                "duplicateOf": self.duplicateOf,
            }

        return {
            "id": str(self.id),
            "title": self.title,
            "description": self.description,
            "severity": self.severity,
            "file_paths": self.file_paths,
        }
