import firebase_admin
from firebase_admin import credentials
import json
import os
from dotenv import load_dotenv

def initialize_firebase():
    """Initialize Firebase with credentials"""
    try:
        # Load environment variables
        load_dotenv()
        
        # First try to use environment variable
        creds_json = os.getenv('FIREBASE_CREDENTIALS')
        
        if creds_json:
            try:
                # Clean and validate the JSON string
                creds_json = creds_json.strip()
                if creds_json.startswith("'") and creds_json.endswith("'"):
                    creds_json = creds_json[1:-1]
                creds_json = creds_json.replace("'", '"')
                
                creds_dict = json.loads(creds_json)
                cred = credentials.Certificate(creds_dict)
            except json.JSONDecodeError as e:
                print("Failed to parse credentials from environment variable, falling back to JSON file")
                print(f"JSON error: {str(e)}")
                creds_json = None
        
        # Fall back to JSON file if environment variable is not available or invalid
        if not creds_json:
            creds_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                'gaia-f1ac4-firebase-adminsdk-e2k9l-18490401f2.json'
            )
            if not os.path.exists(creds_path):
                raise FileNotFoundError(f"Firebase credentials file not found at: {creds_path}")
            
            cred = credentials.Certificate(creds_path)
        
        # Initialize Firebase app
        return firebase_admin.initialize_app(cred)
        
    except Exception as e:
        print(f"Error initializing Firebase: {str(e)}")
        raise 