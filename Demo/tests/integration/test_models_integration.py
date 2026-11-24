"""
Integration tests for AI models.
These tests verify that API keys are valid and can connect to external services.
"""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from app.config import config


class TestClaudeIntegration:
    """Test Claude API connectivity and key validation."""

    @pytest.mark.asyncio
    async def test_claude_api_with_mock(self):
        """Test Claude API using LangChain with mocked response."""
        # Mock the LangChain ChatOpenAI client that talks to LiteLLM
        with patch("app.core.litellm_client.ChatOpenAI") as mock_chat_anthropic:
            mock_client = Mock()
            mock_response = Mock()
            mock_response.content = "Working"
            mock_client.ainvoke = AsyncMock(return_value=mock_response)
            mock_chat_anthropic.return_value = mock_client

            # Import here to avoid circular import issues
            from app.core.claude_model import create_claude_model

            client = create_claude_model(api_key="litellm-test-key")
            response = await client.ainvoke("Hello, please respond with the word 'Working'")

            assert response.content == "Working"
            mock_client.ainvoke.assert_called_once()

    @pytest.mark.asyncio
    async def test_claude_api_error_handling(self):
        """Test error handling for Claude API failures."""
        with patch("app.core.litellm_client.ChatOpenAI") as mock_chat_anthropic:
            mock_client = Mock()
            mock_client.ainvoke = AsyncMock(side_effect=Exception("API Error"))
            mock_chat_anthropic.return_value = mock_client

            from app.core.claude_model import create_claude_model

            client = create_claude_model(api_key="litellm-test-key")

            with pytest.raises(Exception, match="API Error"):
                await client.ainvoke("Test message")

    def test_missing_api_key_handling(self):
        """Test behavior when Claude API key is not configured."""
        with patch("app.core.litellm_client.config") as mock_config:
            mock_config.litellm_server_url = "http://litellm.test"
            mock_config.litellm_api_key = ""

            # Should raise an error when trying to create client without API key
            from app.core.claude_model import create_claude_model

            with pytest.raises(ValueError, match="LITELLM_API_KEY"):
                create_claude_model()

    @pytest.mark.skip(reason="Requires actual API keys - run manually for connectivity testing")
    @pytest.mark.asyncio
    async def test_real_claude_api_connectivity(self):
        """Test actual Claude API connectivity - requires LITELLM_SERVER_URL and LITELLM_API_KEY."""
        if not config.litellm_api_key:
            pytest.skip("Valid LITELLM_API_KEY required for this test")

        from app.core.claude_model import create_claude_model

        client = create_claude_model()
        response = await client.ainvoke("Hello, please respond with exactly the word 'Working'")

        assert "Working" in response.content


class TestGeminiIntegration:
    """Test Gemini API connectivity and key validation."""

    @pytest.mark.asyncio
    async def test_gemini_api_with_mock(self):
        """Test Gemini API using mocked response."""
        with patch("app.core.litellm_client.ChatOpenAI") as mock_chat_gemini:
            mock_client = Mock()
            mock_response = Mock()
            mock_response.content = "Working"
            mock_client.ainvoke = AsyncMock(return_value=mock_response)
            mock_chat_gemini.return_value = mock_client

            from app.core.gemini_model import create_gemini_model

            client = create_gemini_model(api_key="litellm-test-key")
            response = await client.ainvoke("Test message")

            assert response.content == "Working"
            mock_client.ainvoke.assert_called_once()

    @pytest.mark.asyncio
    async def test_gemini_api_error_handling(self):
        """Test error handling for Gemini API failures."""
        with patch("app.core.litellm_client.ChatOpenAI") as mock_chat_gemini:
            mock_client = Mock()
            mock_client.ainvoke = AsyncMock(side_effect=Exception("Gemini API Error"))
            mock_chat_gemini.return_value = mock_client

            from app.core.gemini_model import create_gemini_model

            client = create_gemini_model(api_key="litellm-test-key")

            with pytest.raises(Exception, match="Gemini API Error"):
                await client.ainvoke("Test message")

    def test_missing_gemini_api_key_handling(self):
        """Test behavior when Gemini API key is not configured."""
        with patch("app.core.litellm_client.config") as mock_config:
            mock_config.litellm_server_url = "http://litellm.test"
            mock_config.litellm_api_key = ""

            from app.core.gemini_model import create_gemini_model

            with pytest.raises(ValueError, match="LITELLM_API_KEY"):
                create_gemini_model()

    @pytest.mark.skip(reason="Requires actual API keys - run manually for connectivity testing")
    @pytest.mark.asyncio
    async def test_real_gemini_api_connectivity(self):
        """Test actual Gemini API connectivity - requires LITELLM_SERVER_URL and LITELLM_API_KEY."""
        if not config.litellm_api_key:
            pytest.skip("Valid LITELLM_API_KEY required for this test")

        from app.core.gemini_model import create_gemini_model

        client = create_gemini_model()
        response = await client.ainvoke("Hello, please respond with exactly the word 'Working'")

        assert "Working" in response.content


class TestLiteLLMClient:
    """Test how both models are pointed at the LiteLLM server."""

    @pytest.mark.parametrize(
        ("factory", "model"),
        [
            ("create_claude_model", "anthropic/claude-opus-4-8"),
            ("create_gemini_model", "gemini/gemini-3.8-flash"),
        ],
    )
    def test_model_uses_litellm_server_and_header(self, factory, model):
        """The client targets LITELLM_SERVER_URL and sends the key in X-LiteLLM-API-Key."""
        import app.core.claude_model as claude_model
        import app.core.gemini_model as gemini_model

        module = claude_model if factory == "create_claude_model" else gemini_model
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

    def test_gemini_thinking_level_becomes_reasoning_effort(self):
        """A Gemini 3 thinking level reaches LiteLLM as the reasoning effort."""
        from app.core.gemini_model import create_gemini_model

        with patch("app.core.litellm_client.ChatOpenAI") as mock_chat_openai:
            create_gemini_model(model_name="gemini/gemini-3.8-flash", thinking_level="low")

            assert mock_chat_openai.call_args.kwargs["reasoning_effort"] == "low"
