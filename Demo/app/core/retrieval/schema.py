"""
Schema for retrieval.
"""
from pydantic import BaseModel, Field
from typing import List, Optional


class RetrievedCard(BaseModel):
    """A retrieved card with content."""
    id: str
    content: str
    relpath: str = ""


class RetrievalQuery(BaseModel):
    """Query for retrieval."""
    query: str
    max_cards: int = Field(default=10, description="Maximum number of cards to retrieve")

