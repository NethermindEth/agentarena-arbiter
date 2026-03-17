"""
Schema definitions for context storage.
"""
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any


class Card(BaseModel):
    """Represents a chunk of documentation."""
    id: str
    relpath: str
    char_start: int
    char_end: int
    content: str
    peek_head: str = ""
    peek_tail: str = ""


class Node(BaseModel):
    """Represents a node in the knowledge graph."""
    id: str
    type: str
    label: str
    description: str
    refs: List[str] = Field(default_factory=list)  # Card IDs


class Edge(BaseModel):
    """Represents an edge in the knowledge graph."""
    type: str
    src: str  # Source node ID
    dst: str  # Destination node ID
    refs: List[str] = Field(default_factory=list)  # Card IDs


class KnowledgeGraph(BaseModel):
    """Represents a knowledge graph."""
    name: str
    focus: str
    nodes: List[Node] = Field(default_factory=list)
    edges: List[Edge] = Field(default_factory=list)


class ContextStoragePaths(BaseModel):
    """Paths to context storage files."""
    doc_graphs_dir: Optional[str] = None
    doc_card_store_path: Optional[str] = None

