"""
Documentation processing module.
Handles link validation, fetching, and summarization.
"""
import asyncio
import json
import logging
import re
from typing import List, Optional
import httpx
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field

from app.config import config
from app.core.claude_model import create_claude_model, get_model_config
from app.core.docs_cache import DocsCache

logger = logging.getLogger(__name__)

# Global cache instance
_docs_cache: Optional[DocsCache] = None

def get_docs_cache() -> DocsCache:
    """Get or create global cache instance."""
    global _docs_cache
    if _docs_cache is None:
        _docs_cache = DocsCache()
    return _docs_cache


# Semaphore for rate limiting
LINK_PARSE_SEMAPHORE = asyncio.Semaphore(3)  # Max 3 concurrent requests
LINK_PARSE_DELAY = 1.0  # 1 second delay between requests


class ValidateLinksResponse(BaseModel):
    """Response model for link validation."""
    indexes: List[int] = Field(description="List of relevant link indexes")


FILTER_LINKS_PROMPT = """
You are an expert smart contract auditor specialized at filtering links to ensure that only the most relevant links are used. You are given a list of links. Each link is associated with an index. Your task is to **identify which links are relevant to a smart contract audit**.

## **Instructions:**
A link is considered **relevant** if it provides any of the following:
- Documentation or references related to smart contract programming languages (e.g., Solidity, Cairo).
- Audit frameworks or tools (e.g., Slither, Foundry, Hardhat, etc.).
- Protocol, EIPs or DAO documentation that includes smart contract details (e.g., Kleros, Aave, Uniswap docs).
- Security standards or practices for smart contract development (e.g., OpenZeppelin, SWC registry).

A link is considered **not relevant** if it:
- Points to general social media, news sites, or personal pages with no smart contract context.
- Has no apparent relevance to smart contracts or blockchain security.

## **Output Format:**
Return the indexes of links that are relevant to a smart contract audit in the following JSON format, without any additional text, explanations, comments or chains of thought:
```json
{{
  "indexes": [0, 2, 5]
}}
```

## **Links to analyze:**
```json
{links}
```
"""


SUMMARISE_WEBSEARCH_PROMPT = """
You are an expert smart contract auditor specialized at summarising the content of a given text.

## **Key considerations:**
- Ensure that no details are lost in the process.
- Security related points are crucial and must not be lost.
- Do not cut off any part of the text. You must be as descriptive as possible
- Your output should be well described and easy to understand.
- Your output must have separate section for security related points.
- For EIPs, do not make changes to `Security Considerations` section and ensure that it is **always present** in your output.
- For EIPs, focus on correct implementation of the EIP.
- Skip parts like admin, governance, decentralization, events, etc. that are less relevant and more of a design choice.
- Preserve all code snippets and wrap them with tripple backticks.

## **Text to summarize:**
{text}
"""


async def jina_parse(url: str) -> str:
    """
    Parse content from a URL using Jina API (free tier via httpx).
    
    Args:
        url: The URL to parse
        
    Returns:
        str: The parsed content
    """
    try:
        jina_url = f"https://r.jina.ai/{url}"
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.get(jina_url, headers={
                "X-Return-Format": "markdown"
            })
            response.raise_for_status()
            return response.text
    except Exception as e:
        logger.warning(f"[Jina] Error parsing {url}: {e}")
        # Fallback to simple HTTP fetch
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url)
                response.raise_for_status()
                soup = BeautifulSoup(response.text, 'html.parser')
                # Extract text content
                for script in soup(["script", "style"]):
                    script.decompose()
                return soup.get_text(separator='\n', strip=True)
        except Exception as e2:
            logger.error(f"[Jina] Fallback also failed for {url}: {e2}")
            return ""


async def validate_links(links: List[str]) -> List[str]:
    """
    Validate links using LLM to filter relevant ones.
    
    Args:
        links: List of URLs to validate
        
    Returns:
        List of validated (relevant) links
    """
    if not links:
        return []
    
    try:
        formatted_links = [{"index": i, "link": link} for i, link in enumerate(links)]
        prompt = FILTER_LINKS_PROMPT.format(links=json.dumps(formatted_links, indent=2))
        
        # Use Claude for validation
        model = create_claude_model()
        messages = [
            {"role": "user", "content": prompt}
        ]
        
        response = await model.ainvoke(messages)
        content = response.content if hasattr(response, 'content') else str(response)
        
        # Parse JSON from response
        json_match = re.search(r'\{[^}]*"indexes"[^}]*\}', content, re.DOTALL)
        if json_match:
            result = json.loads(json_match.group())
            indexes = result.get("indexes", [])
            validated = [links[i] for i in indexes if i < len(links)]
            logger.info(f"Validated {len(validated)}/{len(links)} links")
            return validated
        else:
            logger.warning("Could not parse validation response, keeping all links")
            return links
    except Exception as e:
        logger.error(f"Link validation failed: {e}")
        return links


async def summarise_web_page(content: str) -> str:
    """
    Summarize web page content using LLM.
    
    Args:
        content: Raw content from web page
        
    Returns:
        str: Summarized content
    """
    if not content or not content.strip():
        return ""
    
    try:
        prompt = SUMMARISE_WEBSEARCH_PROMPT.format(text=content[:50000])  # Limit to 50k chars
        model = create_claude_model()
        messages = [
            {"role": "user", "content": prompt}
        ]
        
        response = await model.ainvoke(messages)
        summary = response.content if hasattr(response, 'content') else str(response)
        return summary.strip()
    except Exception as e:
        logger.error(f"Summarization failed: {e}")
        return content  # Return original if summarization fails


async def process_single_link(
    link: str,
    link_parse_semaphore: asyncio.Semaphore,
    link_parse_delay: float
) -> str:
    """
    Process a single link with rate limiting and caching.
    
    Args:
        link: URL to process
        link_parse_semaphore: Semaphore for rate limiting
        link_parse_delay: Delay between requests
        
    Returns:
        str: Summarized content
    """
    cache = get_docs_cache()
    
    async with link_parse_semaphore:
        # Check cache
        if cache.check_if_visited(link):
            logger.info(f"[LinkProcessing] Cache hit for: {link[:50]}...")
            cached = await cache.fetch_link_content(link)
            if cached:
                return cached
        
        # Add delay
        await asyncio.sleep(link_parse_delay)
        
        try:
            logger.info(f"[LinkProcessing] Fetching: {link}")
            content = await jina_parse(link)
            
            if not content or not content.strip():
                logger.warning(f"[LinkProcessing] No content for: {link}")
                return ""
            
            # Summarize
            summary = await summarise_web_page(content)
            
            if summary:
                # Cache the summary
                await cache.add_content(link, summary)
                return summary
            else:
                return ""
        except Exception as e:
            logger.exception(f"[LinkProcessing] Error processing {link}: {e}")
            return ""


async def generate_links_summary(links: List[str]) -> List[str]:
    """
    Process multiple links in parallel and return summaries.
    
    Args:
        links: List of URLs to process
        
    Returns:
        List of summarized content strings
    """
    if not links:
        return []
    
    logger.info(f"[LinkProcessing] Processing {len(links)} links")
    
    # Process in parallel with rate limiting
    tasks = [
        process_single_link(link, LINK_PARSE_SEMAPHORE, LINK_PARSE_DELAY)
        for link in links
    ]
    
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    valid_summaries = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            logger.warning(f"[LinkProcessing] Link {i+1} failed: {result}")
        elif result and result.strip():
            valid_summaries.append(result)
    
    logger.info(f"[LinkProcessing] Successfully processed {len(valid_summaries)}/{len(links)} links")
    return valid_summaries


async def process_additional_links(links: List[str]) -> str:
    """
    Main entry point for processing additional links.
    Validates, fetches, and summarizes links.
    
    Args:
        links: List of URLs
        
    Returns:
        str: Combined summarized content
    """
    if not links:
        return ""
    
    # Validate links
    validated_links = await validate_links(links)
    
    if not validated_links:
        logger.info("[LinkProcessing] No valid links after validation")
        return ""
    
    # Generate summaries
    summaries = await generate_links_summary(validated_links)
    
    if not summaries:
        return ""
    
    # Combine summaries
    combined = "\n\n".join(summaries)
    return combined

