"""
Unit tests for the languages stated in the prompts.
"""

from datetime import UTC, datetime

from app.core.languages import language_line, languages_of_files
from app.core.prompt_utils import build_context_section
from app.types import LanguageDefinition, Task, TaskCache

SOLIDITY = LanguageDefinition(
    slug="solidity", caption="Solidity", file_extensions=[".sol"], excluded_extensions=[".t.sol"]
)
RUST = LanguageDefinition(slug="rust", caption="Rust", file_extensions=[".rs"])
JAVASCRIPT = LanguageDefinition(
    slug="javascript", caption="Javascript", file_extensions=[".js", ".mjs"]
)
TASK_LANGUAGES = [SOLIDITY, RUST, JAVASCRIPT]


class TestLanguageLine:
    def test_single_file(self):
        assert language_line(["src/Vault.sol"], TASK_LANGUAGES) == "Language: Solidity"

    def test_two_languages(self):
        line = language_line(["src/lib.rs", "src/Vault.sol"], TASK_LANGUAGES)
        assert line == "Language: Solidity and Rust"

    def test_three_languages_in_task_order(self):
        line = language_line(["web/app.mjs", "src/lib.rs", "src/Vault.sol"], TASK_LANGUAGES)
        assert line == "Language: Solidity, Rust and Javascript"

    def test_repeated_language_named_once(self):
        line = language_line(["a.sol", "b.sol", "c.SOL"], TASK_LANGUAGES)
        assert line == "Language: Solidity"

    def test_excluded_extension_does_not_match(self):
        assert languages_of_files(["test/Vault.t.sol", "src/lib.rs"], TASK_LANGUAGES) == [RUST]

    def test_line_references_are_ignored(self):
        paths = ["src/Vault.sol:42", "src/lib.rs#L10-L20", "app/main.js:10-20 "]
        assert languages_of_files(paths, TASK_LANGUAGES) == [SOLIDITY, RUST, JAVASCRIPT]
        assert languages_of_files(["test/Vault.t.sol:7"], TASK_LANGUAGES) == TASK_LANGUAGES

    def test_no_matching_file_names_all_task_languages(self):
        line = language_line(["README.md"], [SOLIDITY, RUST])
        assert line == "Language: Solidity and Rust"


class TestCodeFence:
    def _cache(self, languages: list[LanguageDefinition]) -> TaskCache:
        return TaskCache(
            taskId="t",
            startTime=datetime.now(UTC),
            deadline=datetime.now(UTC),
            selectedFilesContent="code",
            languages=languages,
        )

    def test_single_language_names_the_fence(self):
        assert "```rust\ncode" in build_context_section(self._cache([RUST]))

    def test_several_languages_leave_the_fence_bare(self):
        assert "```\ncode" in build_context_section(self._cache([SOLIDITY, RUST]))


def test_task_parses_languages_from_backend():
    """The backend's full language entry parses; the fields the arbiter does not use are dropped."""
    task = Task.model_validate(
        {
            "taskId": "t",
            "projectRepo": "r",
            "title": "t",
            "description": "d",
            "status": "Open",
            "startTime": "0",
            "deadline": "1",
            "selectedBranch": "main",
            "selectedFiles": ["src/Vault.sol"],
            "languages": [
                {
                    "slug": "solidity",
                    "caption": "Solidity",
                    "color": "#0000ff",
                    "file_extensions": [".sol"],
                    "excluded_extensions": [".t.sol"],
                }
            ],
        }
    )
    assert task.languages == [SOLIDITY]


TASK_FIELDS = {
    "taskId": "t",
    "projectRepo": "r",
    "title": "t",
    "description": "d",
    "status": "Open",
    "startTime": "0",
    "deadline": "1",
    "selectedBranch": "main",
    "selectedFiles": ["src/Vault.sol"],
}


def test_task_accepts_null_languages():
    """A task the backend serializes with null languages still parses."""
    task = Task.model_validate({**TASK_FIELDS, "languages": None})
    assert task.languages is None


def test_language_accepts_null_extensions():
    """Null extension lists read as empty ones."""
    language = LanguageDefinition.model_validate(
        {"slug": "rust", "caption": "Rust", "file_extensions": None, "excluded_extensions": None}
    )
    assert language.file_extensions == []
    assert language.excluded_extensions == []


class TestTaskCacheLanguages:
    def test_missing_languages_default_to_solidity(self):
        assert TaskCache(taskId="t").languages == [SOLIDITY]

    def test_null_or_empty_languages_default_to_solidity(self):
        assert TaskCache(taskId="t", languages=None).languages == [SOLIDITY]
        assert TaskCache(taskId="t", languages=[]).languages == [SOLIDITY]

    def test_default_lists_are_not_shared(self):
        assert TaskCache().languages[0] is not TaskCache().languages[0]

    def test_cache_without_languages_keeps_the_prompt_line_and_fence(self):
        cache = TaskCache(taskId="t", selectedFilesContent="code")
        assert language_line(["README.md"], cache.languages) == "Language: Solidity"
        assert "```solidity\ncode" in build_context_section(cache)
