"""
Live round trips through the real LiteLLM server, one per model and structured-output method.

They check that the server and each provider accept the production schemas (strict json_schema
with minLength and $defs, or a forced tool call) and that the reply parses into the Pydantic
result. They spend real tokens, so they run only when asked for:

    LITELLM_LIVE_TESTS=1 LITELLM_SERVER_URL=https://... LITELLM_API_KEY=... uv run pytest tests/live -v

A method marked "production" is the one the arbiter uses; the other result tells whether
switching STRUCTURED_OUTPUT_METHOD in that model's module is an option.
"""

import os

import pytest

from app.core import deduplication_model, evaluation_model
from app.core.deduplication_model import DeduplicationResult, create_deduplication_model
from app.core.evaluation_model import EvaluationResult, create_evaluation_model

pytestmark = [
    pytest.mark.external,
    pytest.mark.skipif(
        os.environ.get("LITELLM_LIVE_TESTS") != "1",
        reason="Live LiteLLM calls; set LITELLM_LIVE_TESTS=1 and the real LITELLM_* values",
    ),
]

METHODS = ["function_calling", "json_schema"]

EVALUATION_PROMPT = """
You are a smart contract security expert. Evaluate this single finding.

Finding ID: f-1
Title: Reentrancy in withdraw()
Description: withdraw() sends ETH with call() before setting balances[msg.sender] = 0, so a
malicious contract can re-enter withdraw() and drain the vault.

Return exactly one result for finding_id "f-1".
"""

DEDUPLICATION_PROMPT = """
You group duplicate security findings. These two findings describe the same bug.

Finding ID: f-1
Title: Reentrancy in withdraw()
Description: withdraw() sends ETH before zeroing the balance, allowing re-entry.

Finding ID: f-2
Title: withdraw() can be re-entered
Description: The external call in withdraw() happens before the balance update.

Mark f-2 as a duplicate of f-1, with a one-sentence explanation.
"""


@pytest.mark.parametrize("method", METHODS)
async def test_evaluation_model_round_trip(method):
    """The evaluation model returns an EvaluationResult for one finding through the proxy."""
    production = method == evaluation_model.STRUCTURED_OUTPUT_METHOD
    model = create_evaluation_model().with_structured_output(
        EvaluationResult, method=method, include_raw=True
    )

    response = await model.ainvoke(EVALUATION_PROMPT)

    assert response["parsing_error"] is None, f"production={production}"
    parsed = response["parsed"]
    assert isinstance(parsed, EvaluationResult), f"production={production}: {response['raw']}"
    assert [r.finding_id for r in parsed.results] == ["f-1"]
    assert parsed.results[0].severity in {"High", "Medium", "Low", "Info"}


@pytest.mark.parametrize("method", METHODS)
async def test_deduplication_model_round_trip(method):
    """The deduplication model returns a DeduplicationResult for two duplicates through the proxy."""
    production = method == deduplication_model.STRUCTURED_OUTPUT_METHOD
    model = create_deduplication_model().with_structured_output(
        DeduplicationResult, method=method, include_raw=True
    )

    response = await model.ainvoke(DEDUPLICATION_PROMPT)

    assert response["parsing_error"] is None, f"production={production}"
    parsed = response["parsed"]
    assert isinstance(parsed, DeduplicationResult), f"production={production}: {response['raw']}"
    assert [(r.findingId, r.duplicateOf) for r in parsed.results] == [("f-2", "f-1")]


async def test_production_factories_round_trip():
    """The structured models main.py builds parse a real reply from each model."""
    evaluation = await evaluation_model.create_structured_evaluation_model().ainvoke(
        EVALUATION_PROMPT
    )
    deduplication = await deduplication_model.create_structured_deduplication_model().ainvoke(
        DEDUPLICATION_PROMPT
    )

    assert isinstance(evaluation, EvaluationResult)
    assert isinstance(deduplication["parsed"], DeduplicationResult)
