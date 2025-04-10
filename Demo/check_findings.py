import asyncio
import json
import httpx

async def main():
    async with httpx.AsyncClient() as client:
        print("Fetching findings from test-process-findings task...")
        response = await client.get('http://localhost:8000/tasks/test-process-findings/findings')
        
        if response.status_code == 200:
            findings = response.json()
            print(f"Found {len(findings)} findings")
            print(json.dumps(findings, indent=2))
        else:
            print(f"Error: {response.status_code}")
            print(response.text)

if __name__ == "__main__":
    asyncio.run(main()) 