from datetime import datetime

from pydantic import BaseModel


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
    languages: list[LanguageDefinition] = []
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
    languages: list[LanguageDefinition] = []
    selectedDocsContent: str | None = None
    additionalLinks: list[str] | None = []
    additionalDocs: str | None = None
    qaResponses: list[QAPair] | None = []
