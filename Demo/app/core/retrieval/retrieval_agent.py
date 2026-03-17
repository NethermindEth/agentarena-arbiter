"""
Simplified retrieval agent for knowledge graphs.
"""
import json
import logging
from pathlib import Path
from typing import List, Dict, Optional
import re

from app.core.context_storage.schema import KnowledgeGraph, Card
from app.core.retrieval.schema import RetrievedCard
from app.core.claude_model import create_claude_model

logger = logging.getLogger(__name__)


RETRIEVAL_PROMPT = """
You are a retrieval agent for knowledge graphs. Your goal is to find the most relevant documentation chunks (cards) for a user's query.

## Available Knowledge Graphs:
{graph_list}

## User Query:
{query}

## Your Task:
Analyze the query and the available graphs. Select the most relevant nodes from the graphs that contain information related to the query.

For each graph, identify which nodes are relevant. Return the node IDs that should be retrieved.

## Output Format:
Return a JSON object with this structure:
```json
{{
  "relevant_node_ids": [
    "node_1",
    "node_2",
    "node_3"
  ],
  "reasoning": "Brief explanation of why these nodes are relevant"
}}
```

Focus on nodes that directly relate to the query topic.
"""


def load_graphs(graphs_dir: Path) -> Dict[str, KnowledgeGraph]:
    """Load knowledge graphs from directory."""
    graphs = {}
    if not graphs_dir.exists():
        return graphs
    
    for graph_file in graphs_dir.glob("graph_*.json"):
        try:
            with open(graph_file, 'r') as f:
                data = json.load(f)
                graph = KnowledgeGraph(**data)
                graphs[graph.name] = graph
        except Exception as e:
            logger.warning(f"Failed to load graph {graph_file}: {e}")
    
    return graphs


def load_card_store(card_store_path: Path) -> Dict[str, Card]:
    """Load card store from JSONL file."""
    cards = {}
    if not card_store_path.exists():
        return cards
    
    try:
        with open(card_store_path, 'r') as f:
            for line in f:
                if line.strip():
                    data = json.loads(line)
                    card = Card(**data)
                    cards[card.id] = card
    except Exception as e:
        logger.warning(f"Failed to load card store: {e}")
    
    return cards


def format_graph_list(graphs: Dict[str, KnowledgeGraph]) -> str:
    """Format graphs for prompt."""
    if not graphs:
        return "No graphs available."
    
    graph_descriptions = []
    for graph_name, graph in graphs.items():
        nodes_summary = ", ".join([f"{n.label} ({n.id})" for n in graph.nodes[:10]])
        if len(graph.nodes) > 10:
            nodes_summary += f" ... and {len(graph.nodes) - 10} more"
        
        graph_descriptions.append(
            f"**{graph_name}**: {graph.focus}\n"
            f"  Nodes: {nodes_summary}"
        )
    
    return "\n\n".join(graph_descriptions)


class RetrievalAgent:
    """Simplified retrieval agent for knowledge graphs."""
    
    def __init__(
        self,
        context_store_dir: str,
        card_store_path: str,
    ):
        """
        Initialize retrieval agent.
        
        Args:
            context_store_dir: Directory containing knowledge graphs
            card_store_path: Path to card store JSONL file
        """
        self.context_store_dir = Path(context_store_dir)
        self.card_store_path = Path(card_store_path)
        
        # Load graphs and cards
        self.graphs = load_graphs(self.context_store_dir)
        self.card_store = load_card_store(self.card_store_path)
        
        logger.info(f"Loaded {len(self.graphs)} graphs and {len(self.card_store)} cards")
    
    async def retrieve_context(
        self,
        query: str,
        max_cards: int = 10
    ) -> List[RetrievedCard]:
        """
        Retrieve relevant context for a query.
        
        Args:
            query: Query string
            max_cards: Maximum number of cards to retrieve
            
        Returns:
            List of retrieved cards
        """
        if not self.graphs or not self.card_store:
            logger.warning("No graphs or cards available for retrieval")
            return []
        
        # Format graph list for prompt
        graph_list = format_graph_list(self.graphs)
        
        # Build prompt
        prompt = RETRIEVAL_PROMPT.format(
            graph_list=graph_list,
            query=query
        )
        
        try:
            # Get relevant nodes from LLM
            model = create_claude_model()
            messages = [{"role": "user", "content": prompt}]
            response = await model.ainvoke(messages)
            content = response.content if hasattr(response, 'content') else str(response)
            
            # Extract JSON
            json_match = re.search(r'\{[^}]*"relevant_node_ids"[^}]*\}', content, re.DOTALL)
            if json_match:
                result = json.loads(json_match.group())
                relevant_node_ids = result.get("relevant_node_ids", [])
            else:
                logger.warning("Could not parse retrieval response")
                relevant_node_ids = []
            
            # Collect cards from relevant nodes
            retrieved_cards = []
            seen_card_ids = set()
            
            for graph_name, graph in self.graphs.items():
                for node in graph.nodes:
                    if node.id in relevant_node_ids:
                        # Get cards referenced by this node
                        for card_id in node.refs:
                            if card_id in self.card_store and card_id not in seen_card_ids:
                                card = self.card_store[card_id]
                                retrieved_cards.append(RetrievedCard(
                                    id=card.id,
                                    content=card.content,
                                    relpath=card.relpath
                                ))
                                seen_card_ids.add(card_id)
                                
                                if len(retrieved_cards) >= max_cards:
                                    break
                    
                    if len(retrieved_cards) >= max_cards:
                        break
                
                if len(retrieved_cards) >= max_cards:
                    break
            
            logger.info(f"Retrieved {len(retrieved_cards)} cards for query")
            return retrieved_cards[:max_cards]
        
        except Exception as e:
            logger.error(f"Retrieval failed: {e}")
            return []

