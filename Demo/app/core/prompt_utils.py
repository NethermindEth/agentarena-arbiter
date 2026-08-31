from app.types import TaskCache


# Default language assumed when a task does not declare one, preserving the
# historical Solidity-only behaviour.
DEFAULT_LANGUAGE = "Solidity"

# Mapping of task languages to their markdown code-fence tags. Unknown
# languages fall back to a lower-cased version of the language name.
_FENCE_TAGS = {
    "solidity": "solidity",
    "rust": "rust",
}


def resolve_language(task_cache: TaskCache) -> str:
    """Return the human-readable source language for a task."""
    language = (task_cache.language or "").strip()
    return language or DEFAULT_LANGUAGE


def _fence_tag(language: str) -> str:
    """Return the markdown code-fence tag for a source language."""
    key = language.strip().lower()
    return _FENCE_TAGS.get(key, key or DEFAULT_LANGUAGE.lower())


def build_language_directive(task_cache: TaskCache) -> str:
    """Build the instruction telling the model which language to focus on.

    This is prepended to every task-describing prompt so the model restricts
    its analysis to source code written in the task's language.
    """
    language = resolve_language(task_cache)
    return (
        f"The source code under analysis may be written in many languages. "
        f"However, a finding / vulnerability is in scope only {language} source files. "
        f"Focus your analysis exclusively on {language} source code findings, applying "
        f"the vulnerability classes, idioms, and semantics specific to {language}. "
        f"Disregard issues that do not apply to {language}. However, related files"
        f"could be in different languages (e.g. a Solidity contract having a finding"
        f"which is related to a .json or .js file in a Hardhat project... is allowed)."
    )


def build_context_section(task_cache: TaskCache) -> str:
    """Build the context section for evaluation prompts."""
    context_parts = []

    language = resolve_language(task_cache)

    # Smart contract files
    if task_cache.selectedFilesContent:
        context_parts.append(f"### SMART CONTRACT CODE:\n```\n{task_cache.selectedFilesContent}\n```\n")
    
    # Documentation files
    if task_cache.selectedDocsContent:
        context_parts.append(f"### DOCUMENTATION:\n{task_cache.selectedDocsContent}\n")

    # Additional documentation
    if task_cache.additionalDocs:
        context_parts.append(f"### ADDITIONAL DOCUMENTATION:\n{task_cache.additionalDocs}\n")
    
    # Additional links
    if task_cache.additionalLinks:
        links = '\n'.join([f"- {link}" for link in task_cache.additionalLinks])
        context_parts.append(f"### ADDITIONAL RESOURCES:\n{links}\n")
    
    # Q&A responses
    if task_cache.qaResponses:
        qa_text = "\n\n".join([f"**Q: {qa.question}**\n**A: {qa.answer}**" for qa in task_cache.qaResponses])
        context_parts.append(f"### PROJECT Q&A:\n{qa_text}\n")
    
    return '\n'.join(context_parts) if context_parts else "No smart contract context available."
