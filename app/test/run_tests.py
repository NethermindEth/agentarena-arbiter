"""
Test runner script to execute all tests in sequence.
"""
import asyncio
import sys
import os

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

# Import from config.py instead of using dotenv directly
from app.config import TESTING, DEBUG

# Import test modules
from app.test.test_process_findings import test_process_findings
from app.test.test_max_findings_limit import test_max_findings_limit

async def run_all_tests():
    """Run all test functions in sequence."""
    # Config is already loaded via the config.py import
    print(f"Running tests with TESTING={TESTING}, DEBUG={DEBUG}")
    
    print("=" * 80)
    print("RUNNING SECURITY FINDINGS TESTS")
    print("=" * 80)
    
    # Test: Process findings API
    print("\n\n" + "=" * 40)
    print("TEST : PROCESS FINDINGS API")
    print("=" * 40)
    await test_process_findings()
    
    # Test: Maximum findings limit
    print("\n\n" + "=" * 40)
    print("TEST : MAXIMUM FINDINGS LIMIT")
    print("=" * 40)
    await test_max_findings_limit()
    
    print("\n\n" + "=" * 40)
    print("ALL TESTS COMPLETED")
    print("=" * 40)

if __name__ == "__main__":
    asyncio.run(run_all_tests()) 