from datetime import datetime
from pydantic import BaseModel, Field
from typing import Optional, List

class QAPair(BaseModel):
    """Model for a question-answer pair."""
    question: str
    answer: str

class TaskResponse(BaseModel):
    """Response model for task creation."""
    id: str
    taskId: str
    projectRepo: str
    title: str
    description: str
    bounty: Optional[str] = None
    status: str
    startTime: str
    deadline: str
    selectedBranch: str
    selectedFiles: List[str]
    selectedDocs: Optional[List[str]] = []
    additionalLinks: Optional[List[str]] = []
    additionalDocs: Optional[str] = None
    qaResponses: Optional[List[QAPair]] = []

class TaskCache(BaseModel):
    """Model representing the task cache structure."""
    taskId: Optional[str] = None
    startTime: Optional[datetime] = None
    deadline: Optional[datetime] = None
    selectedFilesContent: Optional[str] = None
    selectedDocsContent: Optional[str] = None
    additionalLinks: Optional[List[str]] = []
    additionalDocs: Optional[str] = None
    qaResponses: Optional[List[QAPair]] = []