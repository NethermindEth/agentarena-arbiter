"""
Simplified knowledge graph builder for documentation.
Creates a hierarchical graph structure from documentation cards.
"""
import json
import logging
import re
from pathlib import Path
from typing import List, Dict, Optional

from app.core.context_storage.schema import Card, Node, Edge, KnowledgeGraph
from app.core.claude_model import create_claude_model

logger = logging.getLogger(__name__)


DOC_GRAPH_DISCOVERY_PROMPT = """
You are building a knowledge graph for smart contract documentation to enable precise retrieval of relevant information.

Given the documentation content samples below, create a hierarchical knowledge graph structure. The graph should organize the documentation into logical topics and concepts.

## Content Samples:
```json
{content_samples}
```

## Your Task:
Design 1-2 knowledge graphs that organize this documentation. Each graph should have:
- A clear focus (what aspect of the documentation it covers)
- High-level topics/concepts as nodes
- Relationships between concepts as edges

## Output Format:
Return a JSON object with this structure:
```json
{{
  "graphs": [
    {{
      "name": "GraphName",
      "focus": "What this graph focuses on",
      "suggested_node_types": ["Section", "Concept", "Feature"],
      "suggested_edge_types": ["contains", "relates_to", "depends_on"]
    }}
  ]
}}
```

Focus on creating a structure that helps retrieve relevant documentation chunks when given a query about a specific finding or topic.
"""


DOC_GRAPH_BUILD_PROMPT = """
You are building a knowledge graph node structure for documentation.

## Graph Focus:
{graph_focus}

## Current Graph State:
{existing_nodes}

## Content Samples:
```json
{content_samples}
```

## Your Task:
Analyze the content samples and either:
1. Add new nodes that represent key topics/concepts
2. Update existing nodes with more details

Each node should:
- Have a clear label (short, descriptive)
- Have a description that summarizes the concept
- Reference card IDs from content_samples that contain relevant information

## Output Format:
Return a JSON object:
```json
{{
  "new_nodes": [
    {{
      "id": "node_1",
      "type": "Section",
      "label": "Node Label",
      "description": "Description of what this node represents",
      "refs": ["card_abc123", "card_def456"]
    }}
  ],
  "node_updates": [
    {{
      "id": "existing_node_id",
      "description": "Updated description"
    }}
  ]
}}
```

Create 5-10 nodes maximum. Focus on high-level concepts that help organize the documentation.
"""


class DocGraphBuilder:
    """Simplified graph builder for documentation."""
    
    def __init__(self, llm_model: Optional[str] = None):
        """
        Initialize graph builder.
        
        Args:
            llm_model: LLM model name (uses default if None)
        """
        self.llm_model = llm_model
        self.graphs: Dict[str, KnowledgeGraph] = {}
        self.card_store: Dict[str, Card] = {}
    
    def _build_card_samples_json(self, cards: List[Card], max_samples: int = 10) -> str:
        """Build JSON representation of card samples."""
        samples = cards[:max_samples]
        card_data = [
            {
                "id": card.id,
                "relpath": card.relpath,
                "content": card.content[:500] + "..." if len(card.content) > 500 else card.content,
            }
            for card in samples
        ]
        return json.dumps(card_data, indent=2)
    
    async def _discover_graphs(self, cards: List[Card]) -> List[Dict]:
        """Discover graph structure from cards."""
        content_samples = self._build_card_samples_json(cards)
        prompt = DOC_GRAPH_DISCOVERY_PROMPT.format(content_samples=content_samples)
        
        try:
            model = create_claude_model()
            messages = [{"role": "user", "content": prompt}]
            response = await model.ainvoke(messages)
            content = response.content if hasattr(response, 'content') else str(response)
            
            # Extract JSON from response - try multiple patterns
            # First try to find JSON in code blocks
            json_patterns = [
                r'```json\s*(\{.*?\})\s*```',  # JSON in code block
                r'```\s*(\{.*?\})\s*```',      # JSON in generic code block
                r'(\{[^}]*"graphs"[^}]*\})',   # JSON with graphs key
            ]
            
            for pattern in json_patterns:
                json_match = re.search(pattern, content, re.DOTALL)
                if json_match:
                    try:
                        result = json.loads(json_match.group(1))
                        graphs = result.get("graphs", [])
                        if graphs:
                            return graphs
                    except json.JSONDecodeError:
                        continue
            
            # If no valid JSON found, try to extract just the JSON object
            json_match = re.search(r'\{[^{}]*"graphs"[^{}]*\[[^\]]*\][^{}]*\}', content, re.DOTALL)
            if json_match:
                try:
                    result = json.loads(json_match.group())
                    return result.get("graphs", [])
                except json.JSONDecodeError:
                    pass
            else:
                # Fallback: create a single default graph
                return [{
                    "name": "DocumentationStructure",
                    "focus": "Organize documentation into logical topics and concepts",
                    "suggested_node_types": ["Section", "Concept", "Feature"],
                    "suggested_edge_types": ["contains", "relates_to"]
                }]
        except Exception as e:
            logger.error(f"Graph discovery failed: {e}")
            # Fallback
            return [{
                "name": "DocumentationStructure",
                "focus": "Organize documentation into logical topics",
                "suggested_node_types": ["Section", "Concept"],
                "suggested_edge_types": ["contains", "relates_to"]
            }]
    
    async def _build_graph_iteration(
        self, graph: KnowledgeGraph, cards: List[Card]
    ) -> bool:
        """Build/refine a graph in one iteration. Returns True if updated."""
        content_samples = self._build_card_samples_json(cards)
        
        existing_nodes_json = json.dumps([
            {
                "id": node.id,
                "type": node.type,
                "label": node.label,
                "description": node.description,
            }
            for node in graph.nodes
        ], indent=2)
        
        prompt = DOC_GRAPH_BUILD_PROMPT.format(
            graph_focus=graph.focus,
            existing_nodes=existing_nodes_json if graph.nodes else "[]",
            content_samples=content_samples
        )
        
        try:
            model = create_claude_model()
            messages = [{"role": "user", "content": prompt}]
            response = await model.ainvoke(messages)
            content = response.content if hasattr(response, 'content') else str(response)
            
            # Extract JSON - try multiple patterns
            json_patterns = [
                r'```json\s*(\{.*?\})\s*```',  # JSON in code block
                r'```\s*(\{.*?\})\s*```',      # JSON in generic code block
                r'(\{[^}]*"new_nodes"[^}]*\})', # JSON with new_nodes key
            ]
            
            result = None
            for pattern in json_patterns:
                json_match = re.search(pattern, content, re.DOTALL)
                if json_match:
                    try:
                        result = json.loads(json_match.group(1))
                        break
                    except json.JSONDecodeError:
                        continue
            
            if result:
                
                # Add new nodes
                for node_data in result.get("new_nodes", []):
                    node = Node(
                        id=node_data.get("id", f"node_{len(graph.nodes)}"),
                        type=node_data.get("type", "Section"),
                        label=node_data.get("label", ""),
                        description=node_data.get("description", ""),
                        refs=node_data.get("refs", [])
                    )
                    graph.nodes.append(node)
                
                # Update existing nodes
                for update_data in result.get("node_updates", []):
                    node_id = update_data.get("id")
                    for node in graph.nodes:
                        if node.id == node_id:
                            if "description" in update_data:
                                node.description = update_data["description"]
                            break
                
                return len(result.get("new_nodes", [])) > 0 or len(result.get("node_updates", [])) > 0
        except Exception as e:
            logger.error(f"Graph building iteration failed: {e}")
        
        return False
    
    async def build(
        self,
        cards: List[Card],
        output_dir: Path,
        max_iterations: int = 3
    ) -> Dict[str, KnowledgeGraph]:
        """
        Build knowledge graphs from cards.
        
        Args:
            cards: List of documentation cards
            output_dir: Directory to save graphs
            max_iterations: Maximum building iterations
            
        Returns:
            Dictionary of graph name to KnowledgeGraph
        """
        # Store cards
        for card in cards:
            self.card_store[card.id] = card
        
        # Discover graphs
        graph_descriptions = await self._discover_graphs(cards)
        
        # Create initial graphs
        for desc in graph_descriptions:
            graph = KnowledgeGraph(
                name=desc["name"],
                focus=desc["focus"],
                nodes=[],
                edges=[]
            )
            self.graphs[graph.name] = graph
        
        # Build graphs iteratively
        for iteration in range(max_iterations):
            logger.info(f"Graph building iteration {iteration + 1}/{max_iterations}")
            had_updates = False
            
            for graph in self.graphs.values():
                updated = await self._build_graph_iteration(graph, cards)
                if updated:
                    had_updates = True
            
            if not had_updates and iteration > 0:
                logger.info("No more updates, stopping")
                break
        
        # Save graphs
        output_dir.mkdir(parents=True, exist_ok=True)
        for graph_name, graph in self.graphs.items():
            graph_file = output_dir / f"graph_{graph_name}.json"
            with open(graph_file, 'w') as f:
                json.dump(graph.model_dump(), f, indent=2)
        
        # Save card store
        card_store_file = output_dir / "card_store.jsonl"
        with open(card_store_file, 'w') as f:
            for card in cards:
                f.write(json.dumps(card.model_dump()) + "\n")
        
        logger.info(f"Built {len(self.graphs)} graphs with {sum(len(g.nodes) for g in self.graphs.values())} total nodes")
        
        return self.graphs

