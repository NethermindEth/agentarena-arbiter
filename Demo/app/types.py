from datetime import datetime

from pydantic import BaseModel


class QAPair(BaseModel):
    """Model for a question-answer pair."""

    question: str
    answer: str


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
    selectedDocs: list[str] | None = []
    additionalLinks: list[str] | None = []
    additionalDocs: str | None = None
    qaResponses: list[QAPair] | None = []
    commitSha: str | None = None


class TaskCache(BaseModel):
    """Model representing the task cache structure."""

    taskId: str | None = None
    title: str | None = None
    description: str | None = None
    startTime: datetime | None = None
    deadline: datetime | None = None
    # Task checkout; the triage copies it into each run's sandboxes and never modifies it.
    repoPath: str | None = None
    # Audit scope: the files the task restricted the review to (empty means the whole repo).
    selectedFiles: list[str] | None = []
    selectedDocs: list[str] | None = []
    selectedFilesContent: str | None = None
    selectedDocsContent: str | None = None
    additionalLinks: list[str] | None = []
    additionalDocs: str | None = None
    qaResponses: list[QAPair] | None = []
