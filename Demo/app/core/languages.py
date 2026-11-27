"""The languages of a task's files, as the evaluation and deduplication prompts state them."""

import re
from collections.abc import Iterable
from pathlib import PurePosixPath

from app.types import LanguageDefinition

# A line reference agents append to a path, as in "src/Vault.sol:42", "src/Vault.sol:10-20" or
# "src/lib.rs#L10-L20"; it hides the file's extension. A colon reference starts only at the
# beginning of a run of digits, colons and hyphens: trying a match at every colon of a long run
# would take time quadratic in the path, which an agent writes.
_LINE_REFERENCE = re.compile(r"(?:(?<![\d:-]):\d[\d:-]*|#L\d[\dL-]*)$")


def matches_language(file_path: str, language: LanguageDefinition) -> bool:
    """
    Tell whether a file is code of the language, matching its path against the language's globs
    as the backend does; case, a trailing line reference and a leading "./" or "/" are ignored.
    """
    path = PurePosixPath(_LINE_REFERENCE.sub("", file_path.strip()).removeprefix("./").lstrip("/"))
    return _matches_any(path, language.source_patterns) and not _matches_any(
        path, language.excluded_patterns
    )


def _matches_any(path: PurePosixPath, patterns: list[str]) -> bool:
    return any(path.full_match(pattern, case_sensitive=False) for pattern in patterns)


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


def join_names(languages: list[LanguageDefinition]) -> str:
    """Join the languages' names as "A", "A and B" or "A, B and C"."""
    names = [lang.name for lang in languages]
    if len(names) <= 1:
        return "".join(names)
    return f"{', '.join(names[:-1])} and {names[-1]}"


def language_line(file_paths: Iterable[str], languages: list[LanguageDefinition]) -> str:
    """
    Build the prompt line naming the languages of the files a prompt refers to.

    Args:
        file_paths: The files a prompt refers to
        languages: The task's languages

    Returns:
        A line such as "Language: Solidity" or "Language: Solidity, Rust and Javascript"
    """
    return f"Language: {join_names(languages_of_files(file_paths, languages))}"
