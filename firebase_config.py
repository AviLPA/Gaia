import json
import os
from firebase_admin import credentials, initialize_app

def initialize_firebase():
    # Get credentials from environment variable
    creds_json = os.getenv('FIREBASE_CREDENTIALS')
    
    if not creds_json:
        raise ValueError("FIREBASE_CREDENTIALS environment variable not found")
    
    # Parse the JSON string into a dictionary
    creds_dict = json.loads(creds_json)
    
    # Create credentials object
    cred = credentials.Certificate(creds_dict)
    
    # Initialize Firebase app
    initialize_app(cred) 