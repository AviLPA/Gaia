import firebase_admin
from firebase_admin import credentials
import os
import json

def initialize_firebase():
    try:
        # First try to use environment variable
        cred_json = os.getenv('FIREBASE_CREDENTIALS')
        if cred_json:
            cred_dict = json.loads(cred_json)
            cred = credentials.Certificate(cred_dict)
        else:
            # Fallback to JSON file
            cred_path = os.path.join(os.path.dirname(__file__), 'serviceAccountKey.json')
            cred = credentials.Certificate(cred_path)
            
        return firebase_admin.initialize_app(cred)
    except Exception as e:
        print(f"Firebase initialization error: {e}")
        raise 