"""The languages of a task's files, as the evaluation and deduplication prompts state them."""

from collections.abc import Iterable

from app.types import LanguageDefinition


def matches_language(file_path: str, language: LanguageDefinition) -> bool:
    """Tell whether a file is code of the language, by its extensions; case is ignored."""
    name = file_path.lower()
    return any(name.endswith(ext.lower()) for ext in language.file_extensions) and not any(
        name.endswith(ext.lower()) for ext in language.excluded_extensions
    )


def languages_of_files(
    file_paths: Iterable[str], languages: list[LanguageDefinition]
) -> list[LanguageDefinition]:
    """
    Pick the languages that at least one of the files is written in.

    Args:
        file_paths: The files a prompt refers to
        languages: The task's languages

    Returns:
        The matching languages in the task's order, or all of the task's languages when no file
        matches any of them, so a prompt always names at least one
    """
    paths = list(file_paths)
    matched = [lang for lang in languages if any(matches_language(p, lang) for p in paths)]
    return matched or list(languages)


def join_captions(languages: list[LanguageDefinition]) -> str:
    """Join the languages' captions as "A", "A and B" or "A, B and C"."""
    captions = [lang.caption for lang in languages]
    if len(captions) <= 1:
        return "".join(captions)
    return f"{', '.join(captions[:-1])} and {captions[-1]}"


def language_line(file_paths: Iterable[str], languages: list[LanguageDefinition]) -> str:
    """
    Build the prompt line naming the languages of the files a prompt refers to.

    Args:
        file_paths: The files a prompt refers to
        languages: The task's languages

    Returns:
        A line such as "Language: Solidity" or "Language: Solidity, Rust and Javascript"
    """
    return f"Language: {join_captions(languages_of_files(file_paths, languages))}"
