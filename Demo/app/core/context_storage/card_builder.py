"""
Card builder for chunking documentation into cards.
"""
import hashlib
import re
import logging
from typing import List, Tuple, Dict
from collections import Counter

from app.core.context_storage.schema import Card

logger = logging.getLogger(__name__)

_STOP_WORDS = {
    "a", "an", "the", "in", "on", "of", "for", "with", "is", "are", "was", "were",
    "be", "been", "being", "and", "or", "but", "if", "to", "it", "he", "she", "they",
    "we", "you", "i", "me", "my", "his", "its", "our", "your", "their", "them", "us",
}


def _generate_card_id(relpath: str, index: int, content: str) -> str:
    """Generate unique card ID."""
    hasher = hashlib.sha256()
    hasher.update(f"{relpath}:{index}:{content[:100]}".encode())
    hash_hex = hasher.hexdigest()[:12]
    return f"card_{hash_hex}"


def _extract_top_tokens(content: str, max_tokens: int = 10) -> List[str]:
    """Extract top tokens from content."""
    clean = "".join(c if c.isalnum() or c.isspace() else " " for c in content)
    tokens = [token for token in clean.lower().split() if token not in _STOP_WORDS]
    if not tokens:
        return []
    token_counts = Counter(tokens)
    return [token for token, _ in token_counts.most_common(max_tokens)]


def _split_doc_into_chunks(
    content: str, min_chunk: int = 500, max_chunk: int = 1500
) -> List[Tuple[str, int, int]]:
    """
    Split documentation into chunks using semantic breaks.
    
    Args:
        content: Document content
        min_chunk: Minimum chunk size in characters
        max_chunk: Maximum chunk size in characters
        
    Returns:
        List of (chunk_content, char_start, char_end) tuples
    """
    chunks: List[Tuple[str, int, int]] = []
    char_offset = 0
    
    # Regex for common doc breaks: headings or double newlines
    section_pattern = re.compile(
        r"(^#{1,3}\s.*$)|(^```.*$)|(\n\n+(?!\s*(\d+\.|\*|-)\s))", re.MULTILINE
    )
    matches = list(section_pattern.finditer(content))
    
    if not matches:
        # Fallback to line-based splitting
        lines = content.split("\n")
        current_chunk = []
        current_size = 0
        for line in lines:
            line_with_newline = line + "\n"
            line_size = len(line_with_newline)
            if current_size + line_size > max_chunk and current_size >= min_chunk:
                chunk_content = "".join(current_chunk)
                chunks.append((chunk_content, char_offset, char_offset + len(chunk_content)))
                char_offset += len(chunk_content)
                current_chunk = []
                current_size = 0
            current_chunk.append(line_with_newline)
            current_size += line_size
        if current_chunk:
            chunk_content = "".join(current_chunk)
            chunks.append((chunk_content, char_offset, char_offset + len(chunk_content)))
        return [c for c in chunks if c[0].strip()]
    
    prev_end = 0
    current_chunk: List[str] = []
    current_size = 0
    
    for match in matches:
        section = content[prev_end : match.start()]
        section_size = len(section)
        
        if current_size + section_size > max_chunk and current_size >= min_chunk:
            chunk_content = "".join(current_chunk)
            chunks.append((chunk_content, char_offset, char_offset + len(chunk_content)))
            char_offset += len(chunk_content)
            current_chunk = []
            current_size = 0
        
        current_chunk.append(section)
        current_size += section_size
        prev_end = match.start()
    
    # Add remaining content
    remaining = content[prev_end:]
    if remaining:
        current_chunk.append(remaining)
        current_size += len(remaining)
    
    if current_size > 0:
        chunk_content = "".join(current_chunk)
        chunks.append((chunk_content, char_offset, char_offset + len(chunk_content)))
    
    # Filter out empty/whitespace chunks
    return [c for c in chunks if c[0].strip()]


def create_doc_cards(contents: Dict[str, str]) -> Tuple[List[Card], Dict[str, List[str]]]:
    """
    Create cards from documentation contents.
    
    Args:
        contents: Dictionary mapping file paths to content
        
    Returns:
        Tuple of (list of cards, mapping of file paths to card IDs)
    """
    cards = []
    file_card_map: Dict[str, List[str]] = {}
    
    for relpath, content in contents.items():
        chunks_with_pos = _split_doc_into_chunks(content, min_chunk=500, max_chunk=1500)
        file_card_ids = []
        
        for chunk_index, (chunk, char_start, char_end) in enumerate(chunks_with_pos):
            card_id = _generate_card_id(relpath, chunk_index, chunk)
            peek_head = chunk[:100] if len(chunk) > 100 else chunk
            peek_tail = chunk[-100:] if len(chunk) > 100 else ""
            
            card = Card(
                id=card_id,
                relpath=relpath,
                char_start=char_start,
                char_end=char_end,
                content=chunk,
                peek_head=peek_head,
                peek_tail=peek_tail,
            )
            cards.append(card)
            file_card_ids.append(card_id)
        
        if file_card_ids:
            file_card_map[relpath] = file_card_ids
    
    logger.info(f"Created {len(cards)} cards from {len(contents)} files")
    return cards, file_card_map

