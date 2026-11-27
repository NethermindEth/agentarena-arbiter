from app.types import TaskCache


def build_context_section(task_cache: TaskCache) -> str:
    """Build the context section for evaluation prompts."""
    context_parts = []

    # Source files; the fence names the language only when the task has a single one. The fence
    # tag is the backend's language slug, which the backend's catalogue keeps equal to the usual
    # Markdown highlight name (solidity, rust, ...); a slug that is not one only leaves the code
    # unhighlighted, the "Language:" line still names the language in words.
    if task_cache.selectedFilesContent:
        languages = task_cache.languages
        fence = languages[0].slug if len(languages) == 1 else ""
        context_parts.append(
            f"### SOURCE CODE:\n```{fence}\n{task_cache.selectedFilesContent}\n```\n"
        )

    # Documentation files
    if task_cache.selectedDocsContent:
        context_parts.append(f"### DOCUMENTATION:\n{task_cache.selectedDocsContent}\n")

    # Additional documentation
    if task_cache.additionalDocs:
        context_parts.append(f"### ADDITIONAL DOCUMENTATION:\n{task_cache.additionalDocs}\n")

    # Additional links
    if task_cache.additionalLinks:
        links = "\n".join([f"- {link}" for link in task_cache.additionalLinks])
        context_parts.append(f"### ADDITIONAL RESOURCES:\n{links}\n")

    # Q&A responses
    if task_cache.qaResponses:
        qa_text = "\n\n".join(
            [f"**Q: {qa.question}**\n**A: {qa.answer}**" for qa in task_cache.qaResponses]
        )
        context_parts.append(f"### PROJECT Q&A:\n{qa_text}\n")

    return "\n".join(context_parts) if context_parts else "No project context available."
