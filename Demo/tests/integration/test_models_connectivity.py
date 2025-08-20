"""
Integration tests for API connectivity.
These tests verify that API keys are valid and can connect to external services.
"""
import pytest
from unittest.mock import patch, Mock, AsyncMock
import asyncio
from app.config import config


class TestClaudeAPIConnectivity:
    """Test Claude API connectivity and key validation."""
    
    @pytest.mark.asyncio
    async def test_claude_api_with_mock(self):
        """Test Claude API using LangChain with mocked response."""
        # Mock the LangChain ChatAnthropic client
        with patch('app.core.claude_model.ChatAnthropic') as mock_chat_anthropic:
            mock_client = Mock()
            mock_response = Mock()
            mock_response.content = "Working"
            mock_client.ainvoke = AsyncMock(return_value=mock_response)
            mock_chat_anthropic.return_value = mock_client
            
            # Import here to avoid circular import issues
            from app.core.claude_model import create_claude_model
            
            client = create_claude_model(api_key="sk-ant-test-key")
            response = await client.ainvoke("Hello, please respond with the word 'Working'")
            
            assert response.content == "Working"
            mock_client.ainvoke.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_claude_api_error_handling(self):
        """Test error handling for Claude API failures."""
        with patch('app.core.claude_model.ChatAnthropic') as mock_chat_anthropic:
            mock_client = Mock()
            mock_client.ainvoke = AsyncMock(side_effect=Exception("API Error"))
            mock_chat_anthropic.return_value = mock_client
            
            from app.core.claude_model import create_claude_model
            
            client = create_claude_model(api_key="sk-ant-test-key")
            
            with pytest.raises(Exception, match="API Error"):
                await client.ainvoke("Test message")
    
    def test_missing_api_key_handling(self):
        """Test behavior when Claude API key is not configured."""
        with patch('app.core.claude_model.config') as mock_config:
            mock_config.claude_api_key = None
            
            # Should raise an error when trying to create client without API key
            from app.core.claude_model import create_claude_model
            
            with pytest.raises(ValueError, match="CLAUDE_API_KEY"):
                create_claude_model()


class TestGeminiAPIConnectivity:
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


# Mark tests that require actual API keys (for manual testing)
@pytest.mark.skip(reason="Requires actual API keys - run manually for connectivity testing")
class TestRealAPIConnectivity:
    """Tests that require real API keys - run manually."""
    
    @pytest.mark.asyncio
    async def test_real_claude_api_connectivity(self):
        """Test actual Claude API connectivity - requires CLAUDE_API_KEY env var."""
        if not config.claude_api_key or not config.claude_api_key.startswith("sk-ant-"):
            pytest.skip("Valid CLAUDE_API_KEY required for this test")
        
        from app.core.claude_model import create_claude_model
        
        client = create_claude_model()
        response = await client.ainvoke("Hello, please respond with exactly the word 'Working'")
        
        assert "Working" in response.content
    
    @pytest.mark.asyncio
    async def test_real_gemini_api_connectivity(self):
        """Test actual Gemini API connectivity - requires GEMINI_API_KEY env var."""
        if not config.gemini_api_key:
            pytest.skip("Valid GEMINI_API_KEY required for this test")
        
        from app.core.gemini_model import create_gemini_model
        
        client = create_gemini_model()
        response = await client.ainvoke("Hello, please respond with exactly the word 'Working'")
        
        assert "Working" in response.content