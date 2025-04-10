"""
Test runner script to execute all tests in sequence.
"""
import asyncio
import sys
import os
from dotenv import load_dotenv

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

# Import test modules
from app.test.test_process_findings import test_process_findings

async def run_all_tests():
    """Run all test functions in sequence."""
    # Load environment variables
    load_dotenv()
    
    print("=" * 80)
    print("RUNNING SECURITY FINDINGS TESTS")
    print("=" * 80)
    
    # Test: Process findings API
    print("\n\n" + "=" * 40)
    print("TEST : PROCESS FINDINGS API")
    print("=" * 40)
    await test_process_findings()
    
    print("\n\n" + "=" * 40)
    print("ALL TESTS COMPLETED")
    print("=" * 40)

if __name__ == "__main__":
    asyncio.run(run_all_tests()) 