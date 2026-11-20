"""
Integration tests for AI models.
These tests verify that API keys are valid and can connect to external services.
(Claude Code CLI / triage tests live in tests/unit/test_triage.py.)
"""
import pytest
from pathlib import Path
from unittest.mock import patch, Mock, AsyncMock
from app.config import config


class TestGeminiIntegration:
    """Test Gemini API connectivity and key validation."""
    
    @pytest.mark.asyncio
    async def test_gemini_api_with_mock(self):
        """Test Gemini API using mocked response."""
        with patch('app.core.gemini_model.ChatGoogleGenerativeAI') as mock_chat_gemini:
            mock_client = Mock()
            mock_response = Mock()
            mock_response.content = "Working"
            mock_client.ainvoke = AsyncMock(return_value=mock_response)
            mock_chat_gemini.return_value = mock_client
            
            from app.core.gemini_model import create_gemini_model
            
            client = create_gemini_model(api_key="test-gemini-key")
            response = await client.ainvoke("Test message")
            
            assert response.content == "Working"
            mock_client.ainvoke.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_gemini_api_error_handling(self):
        """Test error handling for Gemini API failures."""
        with patch('app.core.gemini_model.ChatGoogleGenerativeAI') as mock_chat_gemini:
            mock_client = Mock()
            mock_client.ainvoke = AsyncMock(side_effect=Exception("Gemini API Error"))
            mock_chat_gemini.return_value = mock_client
            
            from app.core.gemini_model import create_gemini_model
            
            client = create_gemini_model(api_key="test-gemini-key")
            
            with pytest.raises(Exception, match="Gemini API Error"):
                await client.ainvoke("Test message")

    def test_missing_gemini_api_key_handling(self):
        """Test behavior when Gemini API key is not configured."""
        with patch('app.core.gemini_model.config') as mock_config:
            mock_config.gemini_api_key = None
            
            from app.core.gemini_model import create_gemini_model
            
            with pytest.raises(ValueError, match="GEMINI_API_KEY"):
                create_gemini_model()

    @pytest.mark.skip(reason="Requires actual API keys - run manually for connectivity testing")
    @pytest.mark.asyncio
    async def test_real_gemini_api_connectivity(self):
        """Test actual Gemini API connectivity - requires GEMINI_API_KEY env var."""
        if not config.gemini_api_key:
            pytest.skip("Valid GEMINI_API_KEY required for this test")
        
        from app.core.gemini_model import create_gemini_model
        
        client = create_gemini_model()
        response = await client.ainvoke("Hello, please respond with exactly the word 'Working'")
        
        assert "Working" in response.content
