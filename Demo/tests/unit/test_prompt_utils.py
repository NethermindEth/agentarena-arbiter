"""
Unit tests for prompt_utils module.
"""
from datetime import datetime, timezone

from app.core.prompt_utils import build_context_section
from app.types import TaskCache, QAPair


class TestBuildContextSection:
    """Test the build_context_section function."""
    
    def test_build_context_with_all_fields(self):
        """Test building context with all fields populated."""
        task_cache = TaskCache(
            taskId="test-task",
            startTime=datetime.now(timezone.utc),
            deadline=datetime.now(timezone.utc),
            selectedFilesContent="contract Test { function example() {} }",
            selectedDocsContent="This is documentation content",
            additionalLinks=["https://example.com", "https://docs.example.com"],
            additionalDocs="Additional documentation text",
            qaResponses=[
                QAPair(question="What is this contract?", answer="It's a test contract"),
                QAPair(question="How does it work?", answer="Through smart contract logic")
            ]
        )
        
        result = build_context_section(task_cache)
        
        # Should contain smart contract section
        assert "### SMART CONTRACT CODE:" in result
        assert "```solidity" in result
        assert "contract Test { function example() {} }" in result
        
        # Should contain documentation section
        assert "### DOCUMENTATION:" in result
        assert "This is documentation content" in result
        
        # Should contain additional docs section
        assert "### ADDITIONAL DOCUMENTATION:" in result
        assert "Additional documentation text" in result
        
        # Should contain links section
        assert "### ADDITIONAL RESOURCES:" in result
        assert "- https://example.com" in result
        assert "- https://docs.example.com" in result
        
        # Should contain Q&A section
        assert "### PROJECT Q&A:" in result
        assert "**Q: What is this contract?**" in result
        assert "**A: It's a test contract**" in result
        assert "**Q: How does it work?**" in result
        assert "**A: Through smart contract logic**" in result
    
    def test_build_context_with_minimal_fields(self):
        """Test building context with only required fields."""
        task_cache = TaskCache(
            taskId="test-task",
            startTime=datetime.now(timezone.utc),
            deadline=datetime.now(timezone.utc),
            selectedFilesContent="contract Minimal {}",
            selectedDocsContent="",
            additionalLinks=[],
            additionalDocs=None,
            qaResponses=[]
        )
        
        result = build_context_section(task_cache)
        
        # Should contain smart contract section
        assert "### SMART CONTRACT CODE:" in result
        assert "contract Minimal {}" in result
        
        # Should not contain other sections
        assert "### DOCUMENTATION:" not in result
        assert "### ADDITIONAL DOCUMENTATION:" not in result
        assert "### ADDITIONAL RESOURCES:" not in result
        assert "### PROJECT Q&A:" not in result
    
    def test_build_context_with_no_content(self):
        """Test building context when no content is available."""
        task_cache = TaskCache(
            taskId="test-task",
            startTime=datetime.now(timezone.utc),
            deadline=datetime.now(timezone.utc),
            selectedFilesContent="",
            selectedDocsContent="",
            additionalLinks=[],
            additionalDocs=None,
            qaResponses=[]
        )
        
        result = build_context_section(task_cache)
        
        assert result == "No smart contract context available."
    
    def test_build_context_with_docs_only(self):
        """Test building context with only documentation."""
        task_cache = TaskCache(
            taskId="test-task",
            startTime=datetime.now(timezone.utc),
            deadline=datetime.now(timezone.utc),
            selectedFilesContent="",
            selectedDocsContent="Only documentation here",
            additionalLinks=[],
            additionalDocs=None,
            qaResponses=[]
        )
        
        result = build_context_section(task_cache)
        
        assert "### DOCUMENTATION:" in result
        assert "Only documentation here" in result
        assert "### SMART CONTRACT CODE:" not in result
    
    def test_build_context_with_single_qa(self):
        """Test building context with single Q&A pair."""
        task_cache = TaskCache(
            taskId="test-task",
            startTime=datetime.now(timezone.utc),
            deadline=datetime.now(timezone.utc),
            selectedFilesContent="contract Single {}",
            selectedDocsContent="",
            additionalLinks=[],
            additionalDocs=None,
            qaResponses=[
                QAPair(question="Single question?", answer="Single answer")
            ]
        )
        
        result = build_context_section(task_cache)
        
        assert "### PROJECT Q&A:" in result
        assert "**Q: Single question?**" in result
        assert "**A: Single answer**" in result
    
    def test_build_context_with_single_link(self):
        """Test building context with single additional link."""
        task_cache = TaskCache(
            taskId="test-task",
            startTime=datetime.now(timezone.utc),
            deadline=datetime.now(timezone.utc),
            selectedFilesContent="contract Link {}",
            selectedDocsContent="",
            additionalLinks=["https://single-link.com"],
            additionalDocs=None,
            qaResponses=[]
        )
        
        result = build_context_section(task_cache)
        
        assert "### ADDITIONAL RESOURCES:" in result
        assert "- https://single-link.com" in result
