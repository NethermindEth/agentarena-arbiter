import logging
from datetime import datetime

from pydantic import BaseModel, Field, ValidationError, ValidationInfo, field_validator

logger = logging.getLogger(__name__)


class QAPair(BaseModel):
    """Model for a question-answer pair."""

    question: str
    answer: str


class LanguageDefinition(BaseModel):
    """
    A language profile of a task, as the backend's task endpoints return it in
    ``selectedProfiles``.

    The backend owns and validates the profiles; this model reads only the fields the prompts use
    and ignores the rest. A file is code of this language when its path matches one of
    ``source_patterns`` and none of ``excluded_patterns``, globs over the whole path matched as
    the backend matches them.
    """

    slug: str
    name: str
    source_patterns: list[str] = []
    excluded_patterns: list[str] = []

    @field_validator("source_patterns", "excluded_patterns", mode="before")
    @classmethod
    def _null_as_empty(cls, value: object) -> object:
        """Read a null pattern list as an empty one."""
        return [] if value is None else value


def default_languages() -> list[LanguageDefinition]:
    """
    Give a task's languages when the backend gives none: Solidity, the language of every task
    before the language profiles. Each call builds new objects, so no two tasks share them.
    """
    return [
        LanguageDefinition(
            slug="solidity",
            name="Solidity",
            source_patterns=["**/*.sol"],
            excluded_patterns=["**/*.t.sol", "**/*.s.sol"],
        )
    ]


class Task(BaseModel):
    """Response model for task creation."""

    taskId: str
    projectRepo: str
    title: str
    description: str
    bounty: str | None = None
    status: str
    startTime: str
    deadline: str
    selectedBranch: str
    selectedFiles: list[str]
    selectedProfiles: list[LanguageDefinition] | None = []
    selectedDocs: list[str] | None = []
    additionalLinks: list[str] | None = []
    additionalDocs: str | None = None
    qaResponses: list[QAPair] | None = []
    commitSha: str | None = None

    @field_validator("selectedProfiles", mode="before")
    @classmethod
    def _drop_invalid_profiles(cls, value: object, info: ValidationInfo) -> object:
        """
        Drop, with a warning, a language profile that fails validation. The approved tasks are
        validated as one list, so a bad profile would otherwise keep every task from being
        scheduled; dropped, it costs only that language.
        """
        if not isinstance(value, list):
            return value
        profiles: list[LanguageDefinition] = []
        for entry in value:
            try:
                profiles.append(LanguageDefinition.model_validate(entry))
            except ValidationError as e:
                # Pydantic shortens the input values in the error; the entry itself may be large.
                task_id = info.data.get("taskId", "<unknown>")
                logger.warning(f"Task {task_id}: dropping an invalid language profile: {e}")
        return profiles


class TaskCache(BaseModel):
    """Model representing the task cache structure."""

    taskId: str | None = None
    startTime: datetime | None = None
    deadline: datetime | None = None
    selectedFilesContent: str | None = None
    # Never empty, so the prompts always name a language and the fence keeps its tag
    languages: list[LanguageDefinition] = Field(default_factory=default_languages)
    selectedDocsContent: str | None = None
    additionalLinks: list[str] | None = []
    additionalDocs: str | None = None
    qaResponses: list[QAPair] | None = []

    @field_validator("languages", mode="before")
    @classmethod
    def _default_when_none_given(cls, value: object) -> object:
        """Fall back to Solidity when the languages are null or empty."""
        return value or default_languages()
