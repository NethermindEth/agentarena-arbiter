"""
Service for creating document context storage.
"""
import logging
import tempfile
from pathlib import Path
from typing import Dict

from app.core.context_storage.card_builder import create_doc_cards
from app.core.context_storage.doc_graph_builder import DocGraphBuilder
from app.core.context_storage.schema import ContextStoragePaths

logger = logging.getLogger(__name__)


async def create_doc_context_storage(
    documents: Dict[str, str],
    project_dir: str = None,
    max_iterations: int = 3,
) -> ContextStoragePaths:
    """
    Create document context storage with knowledge graphs.
    
    Args:
        documents: Dictionary mapping logical filenames to content
        project_dir: Optional project directory (uses temp if None)
        max_iterations: Maximum graph building iterations
        
    Returns:
        ContextStoragePaths with paths to graphs and card store
    """
    if not documents:
        logger.warning("No documents provided for context storage")
        return ContextStoragePaths()
    
    # Create output directory
    if project_dir:
        output_dir = Path(project_dir)
    else:
        output_dir = Path(tempfile.mkdtemp(prefix="doc_context_storage_"))
    
    output_dir.mkdir(parents=True, exist_ok=True)
    graphs_dir = output_dir / "graphs"
    graphs_dir.mkdir(exist_ok=True)
    
    try:
        # Step 1: Create cards from documents
        logger.info("Creating cards from documents...")
        cards, file_card_map = create_doc_cards(documents)
        
        if not cards:
            logger.warning("No cards created from documents")
            return ContextStoragePaths()
        
        # Step 2: Build knowledge graphs
        logger.info("Building knowledge graphs...")
        builder = DocGraphBuilder()
        graphs = await builder.build(
            cards=cards,
            output_dir=graphs_dir,
            max_iterations=max_iterations
        )
        
        # Step 3: Return paths
        card_store_path = graphs_dir / "card_store.jsonl"
        
        logger.info(f"Context storage created at {output_dir}")
        logger.info(f"  - Graphs: {len(graphs)}")
        logger.info(f"  - Cards: {len(cards)}")
        logger.info(f"  - Card store: {card_store_path}")
        
        return ContextStoragePaths(
            doc_graphs_dir=str(graphs_dir),
            doc_card_store_path=str(card_store_path)
        )
    
    except Exception as e:
        logger.error(f"Failed to create context storage: {e}")
        raise

