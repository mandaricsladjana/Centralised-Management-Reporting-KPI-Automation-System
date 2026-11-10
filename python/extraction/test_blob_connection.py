
import os
from dotenv import load_dotenv
from azure.storage.blob import BlobServiceClient

# Load environment variables from .env
load_dotenv()

# Retrieve Azure Storage connection string
connection_string = os.getenv("AZURE_STORAGE_CONNECTION_STRING")

if not connection_string:
    raise ValueError(
        "Azure Storage connection string not found. Check your .env file."
    )

# Establish connection to Azure Blob Storage
blob_service_client = BlobServiceClient.from_connection_string(
    connection_string
)

# Display available containers
print("Successfully connected to Azure Blob Storage!")
print("\nAvailable containers:")

for container in blob_service_client.list_containers():
    print(f"- {container['name']}")
