"""
Integration tests for AI models.
These tests mock the LiteLLM client; tests/live/ makes real calls through the server.
"""

from unittest.mock import AsyncMock, Mock, patch

import pytest


class TestEvaluationModel:
    """Test the evaluation model factory."""

    @pytest.mark.asyncio
    async def test_evaluation_model_with_mock(self):
        """Test the evaluation model with a mocked response."""
        # Mock the LangChain ChatOpenAI client that talks to LiteLLM
        with patch("app.core.litellm_client.ChatOpenAI") as mock_chat_openai:
            mock_client = Mock()
            mock_response = Mock()
            mock_response.content = "Working"
            mock_client.ainvoke = AsyncMock(return_value=mock_response)
            mock_chat_openai.return_value = mock_client

            # Import here to avoid circular import issues
            from app.core.evaluation_model import create_evaluation_model

            client = create_evaluation_model(api_key="litellm-test-key")
            response = await client.ainvoke("Hello, please respond with the word 'Working'")

            assert response.content == "Working"
            mock_client.ainvoke.assert_called_once()

    @pytest.mark.asyncio
    async def test_evaluation_model_error_handling(self):
        """Test error handling for evaluation model failures."""
        with patch("app.core.litellm_client.ChatOpenAI") as mock_chat_openai:
            mock_client = Mock()
            mock_client.ainvoke = AsyncMock(side_effect=Exception("API Error"))
            mock_chat_openai.return_value = mock_client

            from app.core.evaluation_model import create_evaluation_model

            client = create_evaluation_model(api_key="litellm-test-key")

            with pytest.raises(Exception, match="API Error"):
                await client.ainvoke("Test message")

    def test_evaluation_model_missing_api_key(self):
        """Test behavior when the LiteLLM API key is not configured."""
        with patch("app.core.litellm_client.config") as mock_config:
            mock_config.litellm_server_url = "http://litellm.test"
            mock_config.litellm_api_key = ""

            # Should raise an error when trying to create client without API key
            from app.core.evaluation_model import create_evaluation_model

            with pytest.raises(ValueError, match="LITELLM_API_KEY"):
                create_evaluation_model()


class TestDeduplicationModel:
    """Test the deduplication model factory."""

    @pytest.mark.asyncio
    async def test_deduplication_model_with_mock(self):
        """Test the deduplication model with a mocked response."""
        with patch("app.core.litellm_client.ChatOpenAI") as mock_chat_openai:
            mock_client = Mock()
            mock_response = Mock()
            mock_response.content = "Working"
            mock_client.ainvoke = AsyncMock(return_value=mock_response)
            mock_chat_openai.return_value = mock_client

            from app.core.deduplication_model import create_deduplication_model

            client = create_deduplication_model(api_key="litellm-test-key")
            response = await client.ainvoke("Test message")

            assert response.content == "Working"
            mock_client.ainvoke.assert_called_once()

    @pytest.mark.asyncio
    async def test_deduplication_model_error_handling(self):
        """Test error handling for deduplication model failures."""
        with patch("app.core.litellm_client.ChatOpenAI") as mock_chat_openai:
            mock_client = Mock()
            mock_client.ainvoke = AsyncMock(side_effect=Exception("Deduplication API Error"))
            mock_chat_openai.return_value = mock_client

            from app.core.deduplication_model import create_deduplication_model

            client = create_deduplication_model(api_key="litellm-test-key")

            with pytest.raises(Exception, match="Deduplication API Error"):
                await client.ainvoke("Test message")

    def test_deduplication_model_missing_api_key(self):
        """Test behavior when the LiteLLM API key is not configured."""
        with patch("app.core.litellm_client.config") as mock_config:
            mock_config.litellm_server_url = "http://litellm.test"
            mock_config.litellm_api_key = ""

            from app.core.deduplication_model import create_deduplication_model

            with pytest.raises(ValueError, match="LITELLM_API_KEY"):
                create_deduplication_model()


class TestLiteLLMClient:
    """Test how both models are pointed at the LiteLLM server."""

    @pytest.mark.parametrize(
        ("factory", "model"),
        [
            ("create_evaluation_model", "anthropic/claude-opus-4-8"),
            ("create_deduplication_model", "gemini/gemini-3.8-flash"),
        ],
    )
    def test_model_uses_litellm_server_and_header(self, factory, model):
        """The client targets LITELLM_SERVER_URL and sends the key in X-LiteLLM-API-Key."""
        import app.core.deduplication_model as deduplication_model
        import app.core.evaluation_model as evaluation_model

        module = evaluation_model if factory == "create_evaluation_model" else deduplication_model
        with (
            patch("app.core.litellm_client.ChatOpenAI") as mock_chat_openai,
            patch("app.core.litellm_client.config") as mock_config,
        ):
            mock_config.litellm_server_url = "https://litellm.test/"
            mock_config.litellm_api_key = "litellm-test-key"

            getattr(module, factory)(model_name=model, temperature=0.5)

            kwargs = mock_chat_openai.call_args.kwargs
            assert kwargs["model"] == model
            assert kwargs["base_url"] == "https://litellm.test"
            assert kwargs["default_headers"] == {"X-LiteLLM-API-Key": "litellm-test-key"}
            # Both default models think, so the temperature is dropped.
            assert "temperature" not in kwargs

    def test_deduplication_thinking_level_becomes_reasoning_effort(self):
        """A Gemini 3 deduplication thinking level reaches LiteLLM as the reasoning effort."""
        from app.core.deduplication_model import create_deduplication_model

        with patch("app.core.litellm_client.ChatOpenAI") as mock_chat_openai:
            create_deduplication_model(model_name="gemini/gemini-3.8-flash", thinking_level="low")

            assert mock_chat_openai.call_args.kwargs["reasoning_effort"] == "low"

    @pytest.mark.parametrize(
        ("factory", "schema_name", "method"),
        [
            ("create_structured_evaluation_model", "EvaluationResult", "function_calling"),
            ("create_structured_deduplication_model", "DeduplicationResult", "json_schema"),
        ],
    )
    def test_structured_output_method(self, factory, schema_name, method):
        """Each stage requests its schema with the method the live tests validate."""
        import app.core.deduplication_model as deduplication_model
        import app.core.evaluation_model as evaluation_model

        module = (
            evaluation_model
            if factory == "create_structured_evaluation_model"
            else deduplication_model
        )
        model = Mock()

        getattr(module, factory)(model)

        args, kwargs = model.with_structured_output.call_args
        assert args[0].__name__ == schema_name
        assert kwargs["method"] == method
