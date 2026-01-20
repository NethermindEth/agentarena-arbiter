"""
Retrieval context functions for finding-specific retrieval.
"""
import json
import logging
from typing import Optional

from app.core.context_storage.schema import ContextStoragePaths
from app.core.retrieval.retrieval_agent import RetrievalAgent
from app.core.retrieval.schema import RetrievedCard

logger = logging.getLogger(__name__)


def _build_query_for_finding(finding) -> str:
    """
    Build retrieval query from a finding.
    
    Args:
        finding: Finding object with Issue, Description, Contracts, etc.
        
    Returns:
        str: Query string
    """
    # Extract key information from finding
    issue = getattr(finding, 'Issue', '') or getattr(finding, 'title', '')
    description = getattr(finding, 'Description', '') or getattr(finding, 'description', '')
    contracts = getattr(finding, 'Contracts', []) or getattr(finding, 'file_paths', [])
    
    query_parts = []
    if issue:
        query_parts.append(f"Issue: {issue}")
    if description:
        # Use first 200 chars of description
        desc_short = description[:200] + "..." if len(description) > 200 else description
        query_parts.append(f"Description: {desc_short}")
    if contracts:
        contracts_str = ", ".join(contracts[:3])  # Limit to 3 contracts
        query_parts.append(f"Related contracts: {contracts_str}")
    
    query = "Retrieve all documentation relevant to validating the following finding: " + " | ".join(query_parts)
    return query


async def build_retrieved_doc_for_finding(
    finding,
    context_store_paths: ContextStoragePaths,
    max_iterations: int = 5,
) -> Optional[str]:
    """
    Retrieve documentation relevant to a specific finding.
    
    Args:
        finding: Finding object
        context_store_paths: Paths to context storage
        max_iterations: Maximum retrieval iterations (not used in simplified version)
        
    Returns:
        str: Retrieved documentation content, or None if unavailable
    """
    if not context_store_paths or not context_store_paths.doc_graphs_dir or not context_store_paths.doc_card_store_path:
        return None
    
    try:
        # Build query from finding
        query = _build_query_for_finding(finding)
        
        # Create retrieval agent
        agent = RetrievalAgent(
            context_store_dir=context_store_paths.doc_graphs_dir,
            card_store_path=context_store_paths.doc_card_store_path
        )
        
        # Retrieve cards
        cards = await agent.retrieve_context(query=query, max_cards=10)
        
        if not cards:
            return None
        
        # Flatten cards into single string
        content_parts = [card.content for card in cards]
        return "\n\n".join(content_parts)
    
    except Exception as e:
        logger.error(f"Failed to retrieve docs for finding: {e}")
        return None

