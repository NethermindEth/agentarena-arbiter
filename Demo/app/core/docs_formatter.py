"""
Documentation formatter module.
Handles token counting, summarization, and assembly of documentation.
"""
import logging
from dataclasses import dataclass
from typing import List, Optional
import tiktoken

from app.core.claude_model import create_claude_model
from app.core.docs_processing import process_additional_links
from app.types import QAPair

logger = logging.getLogger(__name__)

# Token limits
MAX_TOKENS_PER_DOC_SECTION = 4000  # Max tokens per section before summarization
TOKENS_ENCODING = "cl100k_base"  # Claude encoding


@dataclass
class FileContentData:
    """Data class for file content with token count."""
    content: str
    token_count: int


def count_tokens(text: str) -> int:
    """
    Count tokens in text using tiktoken.
    
    Args:
        text: Text to count
        
    Returns:
        int: Number of tokens
    """
    try:
        enc = tiktoken.get_encoding(TOKENS_ENCODING)
        return len(enc.encode(text))
    except Exception as e:
        logger.warning(f"Token counting failed: {e}, using character estimate")
        # Fallback: rough estimate (4 chars per token)
        return len(text) // 4


SUMMARISE_DEV_DOC_PROMPT = """
You will be given the content of one or more README files from a smart contract repository. Your task is to extract and summarize all relevant information that can support a security analysis or audit of the protocol.

The goal is to capture the developer's **intent**, **design assumptions**, **protocol logic**, and **any implementation details** that may be relevant when assessing the security and correctness of the code. This summary will help detect mismatches between the intended behavior and the actual code implementation.

## **Guidelines for the summary:**
1. **Keep all relevant content** that can help an auditor understand the protocol's purpose, architecture, assumptions, invariants, logic, and usage patterns.
2. **Remove all irrelevant content**, such as marketing fluff, unrelated installation instructions, contribution guides, badges, links to social media, team bios, funding disclosures, etc.
3. **Prioritize information** that helps understand:
   - The **protocol's functionality** and core components
   - **Assumptions and trust models**
   - **Security mechanisms** or threat models described
   - **Administrative controls**, roles, and permissions
   - Details on **upgradeability**, **governance**, or **external dependencies**
   - Examples of **intended usage patterns** and flows
   - Anything that could be useful to understand the design intent and potential sources of vulnerabilities
4. **Do not remove relevant explanations**, even if they are long - keep them if they help understand the architecture, goals, or logic.
5. Structure the summary clearly using only `####`, `#####`, or smaller titles (never higher than `####`). This ensures it will integrate well inside a larger document without conflicting with top-level headings.
6. The final summary should be:
   - **Concise** and well-organized
   - **Fact-based** and faithful to the original README
   - **Readable** and helpful for a security reviewer

## **README Content:**
{readme_content}
"""


async def summarise_dev_doc(content: str) -> str:
    """
    Summarize developer documentation using LLM.
    
    Args:
        content: Documentation content
        
    Returns:
        str: Summarized content
    """
    if not content or not content.strip():
        return ""
    
    try:
        prompt = SUMMARISE_DEV_DOC_PROMPT.format(readme_content=content[:50000])
        model = create_claude_model()
        messages = [
            {"role": "user", "content": prompt}
        ]
        
        response = await model.ainvoke(messages)
        summary = response.content if hasattr(response, 'content') else str(response)
        return summary.strip()
    except Exception as e:
        logger.error(f"Documentation summarization failed: {e}")
        return content  # Return original if summarization fails


async def format_readme_section(readme_contents: List[FileContentData]) -> Optional[str]:
    """
    Format and summarize README files.
    
    Args:
        readme_contents: List of README file contents
        
    Returns:
        str: Formatted README section
    """
    if not readme_contents:
        return None
    
    summaries = []
    for readme in readme_contents:
        if not readme.content or not readme.content.strip():
            continue
        
        try:
            summary = await summarise_dev_doc(readme.content)
            if summary and summary.strip():
                summaries.append(summary)
        except Exception as e:
            logger.warning(f"Failed to summarize README: {e}")
            continue
    
    return "\n\n".join(summaries) if summaries else None


def format_qa_section(qa_data: List[QAPair]) -> Optional[str]:
    """
    Format Q&A data into a string.
    
    Args:
        qa_data: List of Q&A pairs
        
    Returns:
        str: Formatted Q&A section
    """
    if not qa_data:
        return None
    
    formatted_qa = [
        f"Question: {qa.question}\nAnswer: {qa.answer}\n"
        for qa in qa_data
    ]
    
    return "\n".join(formatted_qa) if formatted_qa else None


async def format_additional_docs_section(additional_docs: str) -> str:
    """
    Format additional documentation, summarizing if needed.
    
    Args:
        additional_docs: Additional documentation string
        
    Returns:
        str: Formatted section
    """
    if not additional_docs or not additional_docs.strip():
        return ""
    
    tokens = count_tokens(additional_docs)
    
    if tokens <= MAX_TOKENS_PER_DOC_SECTION:
        return additional_docs
    
    # Summarize if too long
    return await summarise_dev_doc(additional_docs)


async def format_docs_for_scan(
    selected_docs_content: Optional[str] = None,
    additional_docs: Optional[str] = None,
    additional_links: Optional[List[str]] = None,
    qa_responses: Optional[List[QAPair]] = None,
) -> str:
    """
    Main entry point for formatting documentation for scan/evaluation.
    
    Args:
        selected_docs_content: Content from selected documentation files
        additional_docs: Additional documentation string
        additional_links: List of additional URLs
        qa_responses: List of Q&A pairs
        
    Returns:
        str: Formatted documentation string
    """
    formatted_sections = []
    
    # 1. Selected documentation files (README, etc.)
    if selected_docs_content and selected_docs_content.strip():
        readme_data = FileContentData(
            content=selected_docs_content,
            token_count=count_tokens(selected_docs_content)
        )
        readme_section = await format_readme_section([readme_data])
        if readme_section:
            formatted_sections.append(f"### Developer Documentation\n{readme_section}\n")
    
    # 2. Q&A responses
    if qa_responses:
        qa_section = format_qa_section(qa_responses)
        if qa_section:
            formatted_sections.append(f"### Questions & Answers\n{qa_section}\n")
    
    # 3. Additional documentation
    if additional_docs and additional_docs.strip():
        additional_section = await format_additional_docs_section(additional_docs)
        if additional_section:
            formatted_sections.append(f"### Additional Docs\n{additional_section}\n")
    
    # 4. Additional links (processed and summarized)
    if additional_links:
        logger.info(f"Processing {len(additional_links)} additional links")
        links_content = await process_additional_links(additional_links)
        if links_content:
            formatted_sections.append(f"### Additional context from provided URLs:\n{links_content}\n")
    
    if not formatted_sections:
        return "None Given"
    
    return "\n\n".join(formatted_sections)

