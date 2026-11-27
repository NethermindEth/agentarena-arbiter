"""
Unit tests for the languages stated in the prompts.
"""

import logging
import time
from datetime import UTC, datetime

import pytest

from app.core.languages import language_line, languages_of_files
from app.core.prompt_utils import build_context_section
from app.types import LanguageDefinition, Task, TaskCache

SOLIDITY = LanguageDefinition(
    slug="solidity",
    name="Solidity",
    source_patterns=["**/*.sol"],
    excluded_patterns=["**/*.t.sol", "**/*.s.sol"],
)
RUST = LanguageDefinition(slug="rust", name="Rust", source_patterns=["**/*.rs"])
JAVASCRIPT = LanguageDefinition(
    slug="javascript", name="Javascript", source_patterns=["**/*.js", "**/*.mjs"]
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

    def test_excluded_pattern_does_not_match(self):
        assert languages_of_files(["test/Vault.t.sol", "src/lib.rs"], TASK_LANGUAGES) == [RUST]

    def test_folder_exclusion(self):
        rust = LanguageDefinition(
            slug="rust", name="Rust", source_patterns=["**/*.rs"], excluded_patterns=["**/tests/**"]
        )
        assert languages_of_files(["crates/a/tests/it.rs"], [SOLIDITY, rust]) == [SOLIDITY, rust]
        assert languages_of_files(["crates/a/src/lib.rs"], [SOLIDITY, rust]) == [rust]

    def test_root_file_and_leading_slash_match(self):
        assert languages_of_files(["Vault.sol"], [SOLIDITY, RUST]) == [SOLIDITY]
        assert languages_of_files(["./src/lib.rs"], [SOLIDITY, RUST]) == [RUST]
        assert languages_of_files(["/src/lib.rs"], [SOLIDITY, RUST]) == [RUST]

    def test_line_references_are_ignored(self):
        paths = ["src/Vault.sol:42", "src/lib.rs#L10-L20", "app/main.js:10-20 "]
        assert languages_of_files(paths, TASK_LANGUAGES) == [SOLIDITY, RUST, JAVASCRIPT]
        assert languages_of_files(["test/Vault.t.sol:7"], TASK_LANGUAGES) == TASK_LANGUAGES

    def test_long_line_reference_takes_linear_time(self):
        path = "x" + ":1" * 32000 + ".sol"
        start = time.perf_counter()
        languages_of_files([path], TASK_LANGUAGES)
        assert time.perf_counter() - start < 1

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


# A language profile as the backend's ArbiterTask serializes it in ``selectedProfiles``
# (agentarena-app, backend/models/arbiter_tasks.py and models/language_profiles.py).
BACKEND_SOLIDITY = {
    "slug": "solidity",
    "name": "Solidity",
    "color": "#aa6746",
    "source_patterns": ["**/*.sol"],
    "excluded_patterns": ["**/*.t.sol", "**/*.s.sol"],
}
BACKEND_RUST = {
    "slug": "rust",
    "name": "Rust",
    "color": "#dea584",
    "source_patterns": ["**/*.rs"],
    "excluded_patterns": ["**/tests/**"],
}


def test_task_parses_profiles_from_backend():
    """The backend's profiles parse; the fields the arbiter does not use are dropped."""
    task = Task.model_validate(
        {**TASK_FIELDS, "selectedProfiles": [BACKEND_SOLIDITY, BACKEND_RUST]}
    )
    assert task.selectedProfiles == [
        SOLIDITY,
        LanguageDefinition(
            slug="rust", name="Rust", source_patterns=["**/*.rs"], excluded_patterns=["**/tests/**"]
        ),
    ]


def test_task_accepts_null_profiles():
    """A task the backend serializes with null profiles still parses."""
    task = Task.model_validate({**TASK_FIELDS, "selectedProfiles": None})
    assert task.selectedProfiles is None


@pytest.mark.parametrize(
    "bad_entry",
    [{**BACKEND_RUST, "name": None}, {"slug": "rust"}, "rust", None],
)
def test_task_drops_an_invalid_profile(bad_entry, caplog):
    """An invalid profile is dropped with a warning; the task and its other profiles parse."""
    with caplog.at_level(logging.WARNING, logger="app.types"):
        task = Task.model_validate(
            {**TASK_FIELDS, "selectedProfiles": [BACKEND_SOLIDITY, bad_entry]}
        )
    assert task.selectedProfiles == [SOLIDITY]
    assert "Task t: dropping an invalid language profile" in caplog.text


def test_language_accepts_null_patterns():
    """Null pattern lists read as empty ones."""
    language = LanguageDefinition.model_validate(
        {"slug": "rust", "name": "Rust", "source_patterns": None, "excluded_patterns": None}
    )
    assert language.source_patterns == []
    assert language.excluded_patterns == []


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
