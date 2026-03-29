import os

# Set a new environment variable (must be a string)
os.environ["API_KEY"] = "your_secret_api_key"
os.environ["DEBUG_MODE"] = "True" # Even booleans should be strings

# Access the variable later in the script
api_key = os.environ["API_KEY"]
print(f"API Key: {api_key}")
