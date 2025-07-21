"""
Test script to verify that the Claude API key is valid.
"""
import os
import asyncio
import sys
from pathlib import Path

# Import config directly instead of using dotenv
from app.config import config

# Import Claude API client
try:
    from langchain_anthropic import ChatAnthropic
    USING_LANGCHAIN = True
except ImportError:
    try:
        import anthropic
        USING_LANGCHAIN = False
    except ImportError:
        print("❌ Error: Neither LangChain nor Anthropic SDK found")
        print("   Run: pip install anthropic langchain-anthropic")
        sys.exit(1)

async def test_claude_api():
    """Test the Claude API connection with the configured API key."""
    try:
        # Get API key from config
        api_key = config.claude_api_key
        if not api_key:
            print("❌ CLAUDE_API_KEY not found in configuration")
            return False
        
        # Basic validation
        if not api_key.startswith("sk-ant-"):
            print("❌ Invalid API key format - should start with 'sk-ant-'")
            return False
            
        masked_key = f"{api_key[:8]}...{api_key[-4:]}" if len(api_key) > 12 else "[too short]"
        print(f"🔑 Using API key: {masked_key}")
        print(f"🤖 Using model: {config.claude_model}")
        
        # Test API using available client
        if USING_LANGCHAIN:
            return await test_with_langchain(api_key)
        else:
            return await test_with_anthropic(api_key)
            
    except Exception as e:
        print(f"❌ Error during API test: {str(e)}")
        return False

async def test_with_langchain(api_key):
    """Test with LangChain Anthropic integration."""
    try:
        client = ChatAnthropic(
            model=config.claude_model,
            anthropic_api_key=api_key,
            temperature=config.claude_temperature
        )
        
        print("🔄 Testing with LangChain...")
        response = await client.ainvoke("Hello, please respond with the word 'Working'")
        
        if hasattr(response, 'content') and response.content:
            print(f"✅ Response received: {response.content[:30]}...")
            return True
        
        print("❌ Unexpected response format")
        return False
            
    except Exception as e:
        print(f"❌ API request failed: {str(e)}")
        return False

async def test_with_anthropic(api_key):
    """Test with native Anthropic SDK."""
    try:
        client = anthropic.Anthropic(api_key=api_key)
        
        print("🔄 Testing with Anthropic SDK...")
        message = await client.messages.create(
            model=config.claude_model,
            max_tokens=config.claude_max_tokens,
            messages=[{"role": "user", "content": "Hello, please respond with the word 'Working'"}]
        )
        
        if message and hasattr(message, 'content'):
            content = message.content[0].text if hasattr(message.content[0], 'text') else message.content[0].get('text', '')
            print(f"✅ Response received: {content[:30]}...")
            return True
        
        print("❌ Unexpected response format")
        return False
    
    except Exception as e:
        print(f"❌ API request failed: {str(e)}")
        return False

if __name__ == "__main__":
    print("🔑 Testing Claude API key...\n")
    
    result = asyncio.run(test_claude_api())
    
    if result:
        print("\n✅ API key is valid and working correctly")
    else:
        print("\n❌ API key validation failed")
        print("\n💡 To fix Claude API key issues:")
        print("   1. Get a valid API key from https://console.anthropic.com/keys")
        print("   2. Add to .env file: CLAUDE_API_KEY=sk-ant-api03-xxxxxxxxxxxx") 