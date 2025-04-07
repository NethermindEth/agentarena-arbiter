# This script is for test purposes only.
# It creates test data in the MongoDB database for the smart contract vulnerability submissions API.
from pymongo import MongoClient
from datetime import datetime

# Connect to MongoDB
client = MongoClient("mongodb://localhost:27017")
db = client.get_database("smart_contract_db")

# Drop existing collections to start fresh
db.contracts.drop()

# Create test contract
db.contracts.insert_one({
    "task_id": "project-123",  # Use the same ID you plan to use in your test request
    "name": "TestContract",
    "code": "contract Test { /* contract code here */ }",
    "language": "Solidity",
    "created_at": datetime.utcnow().isoformat(),
    "updated_at": datetime.utcnow().isoformat()
})

print("Test data created successfully!")