from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class QAPair(BaseModel):
    """Model for a question-answer pair."""

    question: str
    answer: str


class LanguageDefinition(BaseModel):
    """
    A language of a task, as the backend's task endpoints return it.

    The backend owns and validates the language catalogue; this model reads only the fields the
    prompts use and ignores the rest. A file is code of this language when its name ends with one
    of ``file_extensions`` and with none of ``excluded_extensions``.
    """

    slug: str
    caption: str
    file_extensions: list[str] = []
    excluded_extensions: list[str] = []

    @field_validator("file_extensions", "excluded_extensions", mode="before")
    @classmethod
    def _null_as_empty(cls, value: object) -> object:
        """Read a null extension list as an empty one."""
        return [] if value is None else value


def default_languages() -> list[LanguageDefinition]:
    """
    Give a task's languages when the backend gives none: Solidity, the language of every task
    before the language catalogue. Each call builds new objects, so no two tasks share them.
    """
    return [
        LanguageDefinition(
            slug="solidity",
            caption="Solidity",
            file_extensions=[".sol"],
            excluded_extensions=[".t.sol"],
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
    languages: list[LanguageDefinition] | None = []
    selectedDocs: list[str] | None = []
    additionalLinks: list[str] | None = []
    additionalDocs: str | None = None
    qaResponses: list[QAPair] | None = []
    commitSha: str | None = None


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
