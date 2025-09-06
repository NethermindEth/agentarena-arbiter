"""
Pytest configuration and shared fixtures.
"""
import pytest
import asyncio
from datetime import datetime, timezone
from typing import List
from unittest.mock import Mock, AsyncMock, patch
from bson import ObjectId

from app.models.finding_db import Status, Severity
from app.types import TaskCache


@pytest.fixture
def event_loop():
    """Create an instance of the default event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def sample_task_cache() -> TaskCache:
    """Create a sample task cache for testing."""
    return TaskCache(
        taskId="test-task-123",
        startTime=datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
        deadline=datetime(2030, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
        selectedFilesContent="contract Test { function withdraw() {} }",
        selectedDocsContent="Documentation for the test contract",
        additionalLinks=[],
        additionalDocs="",
        qaResponses=[]
    )


@pytest.fixture
def sample_findings() -> List[Mock]:
    """Create mock findings for testing (avoiding database initialization)."""
    base_time = datetime.now(timezone.utc)
    
    def create_mock_finding(title: str, description: str, severity: Severity, 
                           file_paths: List[str], agent_id: str) -> Mock:
        """Helper function to create a mock finding with standard initialization."""
        finding = Mock()
        finding._id = ObjectId()
        finding.str_id = str(finding._id)
        finding.title = title
        finding.description = description
        finding.severity = severity
        finding.file_paths = file_paths
        finding.agent_id = agent_id
        finding.status = Status.PENDING
        finding.created_at = base_time
        finding.updated_at = base_time
        finding.model_dump = lambda: {
            "_id": finding.str_id,
            "title": finding.title,
            "description": finding.description,
            "severity": finding.severity.value,
            "file_paths": finding.file_paths,
            "agent_id": finding.agent_id,
            "status": finding.status.value,
            "created_at": finding.created_at.isoformat(),
            "updated_at": finding.updated_at.isoformat()
        }
        return finding
    
    # Create mock findings with different data
    findings = [
        create_mock_finding(
            title="Reentrancy vulnerability in withdraw function",
            description="The withdraw function allows external calls before updating balances",
            severity=Severity.HIGH,
            file_paths=["contracts/Vault.sol"],
            agent_id="agent_alice"
        ),
        create_mock_finding(
            title="Reentrancy in withdrawal method",
            description="External call made before balance reduction in withdraw",
            severity=Severity.HIGH,
            file_paths=["contracts/Vault.sol"],
            agent_id="agent_bob"
        ),
        create_mock_finding(
            title="Missing access control",
            description="Admin function lacks proper authorization",
            severity=Severity.MEDIUM,
            file_paths=["contracts/Access.sol"],
            agent_id="agent_alice"
        )
    ]
    
    return findings


@pytest.fixture
def mock_mongodb():
    """Mock MongoDB handler."""
    mock = AsyncMock()
    mock.connect = AsyncMock()
    mock.close = AsyncMock()
    mock.create_finding = AsyncMock()
    mock.get_findings = AsyncMock()
    mock.update_finding = AsyncMock()
    mock.delete_agent_findings = AsyncMock(return_value=0)
    mock.get_metadata = AsyncMock(return_value=None)
    mock.set_metadata = AsyncMock()
    return mock


@pytest.fixture
def mock_httpx_client():
    """Mock httpx AsyncClient for HTTP requests."""
    mock = AsyncMock()
    mock_client = AsyncMock()
    mock_client.get = AsyncMock()
    mock_client.post = AsyncMock()
    mock.__aenter__.return_value = mock_client
    return mock


@pytest.fixture
def client(sample_task_cache, mock_mongodb):
    """Create FastAPI test client with mocked dependencies."""
    # Import here to avoid circular imports and initialization issues
    from fastapi.testclient import TestClient
    from app.main import app
    
    # Mock the database and other dependencies before creating the client
    with patch('app.main.mongodb', mock_mongodb), \
         patch('app.main.task_cache_map', { sample_task_cache.taskId: sample_task_cache }), \
         patch('app.main.agents_cache') as mock_agents:
        
        # Configure mock_agents to behave like a list
        test_agents = [{"agent_id": "test-agent", "api_key": "test-key"}]
        mock_agents.__iter__ = lambda self: iter(test_agents)
        mock_agents.__len__ = lambda self: len(test_agents)
        mock_agents.__getitem__ = lambda self, key: test_agents[key]

        # Create client without triggering lifespan events that need real DB
        client = TestClient(app, base_url="http://testserver")
        
        # Attach the mock_mongodb to the client so tests can access it if needed
        client.mock_db = mock_mongodb
        
        yield client
