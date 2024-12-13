import firebase_admin
from firebase_admin import credentials, firestore
import qrcode
from flask import Flask, jsonify, request, render_template_string, send_file, render_template
import json
from datetime import datetime
import uuid
import socket
import os
import gc  # For garbage collection
import logging
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from io import BytesIO

# Set up logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Initialize Flask app with custom template folder
template_dir = '/Users/avicomputer/Desktop/Start Ups/Gaia/Code/Gaia/templates'
app = Flask(__name__, 
           template_folder=template_dir,
           static_folder=os.path.join(os.path.dirname(template_dir), 'static'))

# Update UPLOAD_FOLDER path to match the project structure
UPLOAD_FOLDER = os.path.join(os.path.dirname(template_dir), 'static')
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

# Initialize Firebase with timeout settings
try:
    print("Initializing Firebase...")
    creds_path = '/Users/avicomputer/Desktop/Start Ups/Gaia/Code/gaia-f1ac4-firebase-adminsdk-e2k9l-18490401f2.json'
    print(f"Looking for credentials at: {creds_path}")
    
    # Add timeout options
    options = {
        'timeoutSeconds': 30,
        'cacheSizeBytes': 1024 * 1024  # 1MB cache
    }
    cred = credentials.Certificate(creds_path)
    firebase_admin.initialize_app(cred, options={'databaseURL': 'https://gaia-f1ac4.firebaseio.com'})
    db = firestore.client()
    print("Firebase initialized successfully!")
except Exception as e:
    print(f"Error initializing Firebase: {e}")
    raise

# Keep your existing HOME_TEMPLATE here
HOME_TEMPLATE = """
<!DOCTYPE html>
<html>
... (your existing HOME_TEMPLATE HTML code) ...
</html>
"""

class ReceiptManager:
    def __init__(self):
        self.db = firestore.client()
    
    def upload_receipt(self, receipt_data):
        """Upload a receipt to Firestore"""
        logger.debug("Starting receipt upload...")
        receipt_id = str(uuid.uuid4())
        receipt_data['timestamp'] = datetime.now().isoformat()
        
        try:
            logger.debug(f"Attempting to save receipt {receipt_id} to Firebase...")
            self.db.collection('receipts').document(receipt_id).set(receipt_data)
            logger.info(f"Successfully saved receipt {receipt_id}")
            return receipt_id
        except Exception as e:
            logger.error(f"Failed to save receipt: {e}", exc_info=True)
            return None
    
    def get_receipt(self, receipt_id):
        """Retrieve a receipt from Firestore"""
        try:
            print(f"Attempting to retrieve receipt: {receipt_id}")
            doc = self.db.collection('receipts').document(receipt_id).get()
            if doc.exists:
                print(f"Found receipt: {doc.to_dict()}")
                return doc.to_dict()  # Ensure this includes 'items' as a list
            print("Receipt not found")
            return None
        except Exception as e:
            print(f"Error retrieving receipt: {e}")
            return None
    
    def generate_qr(self, receipt_id, base_url=None):
        """Generate QR code for a receipt with validation token"""
        try:
            logger.debug(f"Starting QR generation for receipt {receipt_id}...")
            
            if base_url is None:
                # Use the specific local IP address instead of hostname
                base_url = "http://192.168.1.23:8080"
            
            # Generate a validation token
            validation_token = uuid.uuid4().hex
            
            # Store the validation token with the receipt
            self.db.collection('receipts').document(receipt_id).update({
                'validation_token': validation_token
            })
            
            # Create URL with validation token
            url = f"{base_url}/receipt/{receipt_id}?token={validation_token}"
            logger.debug(f"Generated QR URL: {url}")
            
            # Create QR code
            qr = qrcode.QRCode(
                version=1,
                error_correction=qrcode.constants.ERROR_CORRECT_H,
                box_size=8,
                border=4
            )
            qr.add_data(url)
            qr.make(fit=True)
            
            qr_image = qr.make_image(fill_color="black", back_color="white")
            qr_path = os.path.join(UPLOAD_FOLDER, f"receipt_qr_{receipt_id}.png")
            qr_image.save(qr_path)
            
            return True
        except Exception as e:
            logger.error(f"Failed to generate QR code: {e}")
            return False

class TransactionManager:
    def __init__(self):
        self.db = firestore.client()
    
    def create_transaction(self, receipt_id, payment_data):
        """Create a transaction record with payment details"""
        transaction_id = f"TXN-{uuid.uuid4().hex[:8].upper()}"
        
        transaction_data = {
            'transaction_id': transaction_id,
            'receipt_id': receipt_id,
            'timestamp': datetime.now().isoformat(),
            'payment_method': payment_data['payment_method'],
            'card_info': {
                # Store last 4 digits only for security
                'last_four': payment_data.get('card_number', '')[-4:] if payment_data.get('card_number') else None,
                'exp_date': payment_data.get('card_exp'),
                # Don't store CVV
            },
            'status': 'completed'
        }
        
        try:
            self.db.collection('transactions').document(transaction_id).set(transaction_data)
            return transaction_id
        except Exception as e:
            print(f"Error creating transaction: {e}")
            return None
    
    def get_transaction(self, transaction_id):
        """Retrieve transaction details"""
        try:
            doc = self.db.collection('transactions').document(transaction_id).get()
            if doc.exists:
                return doc.to_dict()
            return None
        except Exception as e:
            print(f"Error retrieving transaction: {e}")
            return None

class ScanManager:
    def __init__(self):
        self.db = firestore.client()
    
    def record_scan(self, receipt_id, device_uid, token):
        """Record a new scan of a receipt"""
        try:
            logger.debug(f"Starting record_scan for receipt_id: {receipt_id}, device_uid: {device_uid}")
            
            # Ensure we have a device_uid
            if not device_uid:
                device_uid = str(uuid.uuid4())
                logger.debug(f"Generated new device_uid: {device_uid}")
            
            # Get user agent info
            user_agent = request.headers.get('User-Agent', '')
            device_type = 'Mobile' if 'Mobile' in user_agent else 'Desktop'
            browser = 'Safari' if 'Safari' in user_agent else 'Chrome' if 'Chrome' in user_agent else 'Unknown Browser'
            
            # Create more detailed device info
            device_info = {
                'model': f"{device_type} - {browser}",
                'browser': browser,
                'platform': device_type,
                'device_uid': device_uid,
                'user_agent': user_agent,  # Store full user agent string
                'timestamp': datetime.now().isoformat()
            }
            
            logger.debug(f"Created device info: {device_info}")
            
            scan_data = {
                'receipt_id': receipt_id,
                'device_uid': device_uid,
                'timestamp': datetime.now().isoformat(),
                'token': token,
                'device_info': device_info
            }
            
            logger.debug(f"Attempting to save scan data: {scan_data}")
            
            # Store in 'scans' subcollection
            scan_ref = self.db.collection('receipts').document(receipt_id)\
                .collection('scans').add(scan_data)
            
            logger.debug(f"Scan saved with ID: {scan_ref[1].id}")
            
            # Store in devices collection
            device_ref = self.db.collection('devices').document(device_uid)
            device_ref.set({
                'last_seen': datetime.now().isoformat(),
                'last_receipt': receipt_id,
                'device_info': scan_data['device_info']
            }, merge=True)
            
            logger.debug("Device info updated successfully")
            return True
            
        except Exception as e:
            logger.error(f"Failed to record scan: {e}", exc_info=True)
            return False
    
    def get_scans(self, receipt_id):
        """Get all scans for a receipt"""
        try:
            logger.debug(f"Fetching scans for receipt: {receipt_id}")
            scans = []
            
            # Get scans from the subcollection
            scan_refs = self.db.collection('receipts')\
                .document(receipt_id)\
                .collection('scans')\
                .order_by('timestamp', direction=firestore.Query.DESCENDING)\
                .stream()  # Use stream() instead of get() for better performance
            
            for scan in scan_refs:
                scan_data = scan.to_dict()
                logger.debug(f"Found scan: {scan_data}")
                
                # Ensure all required fields are present
                if 'device_info' not in scan_data:
                    scan_data['device_info'] = {}
                
                # Ensure device_uid is included in the scan data
                if 'device_uid' in scan_data:
                    scan_data['device_info']['device_uid'] = scan_data['device_uid']
                
                scans.append(scan_data)
            
            logger.info(f"Retrieved {len(scans)} scans for receipt {receipt_id}")
            
            # Only return placeholder if no real scans found
            if not scans:
                logger.debug("No scans found, returning placeholder")
                current_time = datetime.now()
                placeholder_scan = {
                    'device_info': {
                        'model': 'No Previous Scans',
                        'browser': 'N/A',
                        'platform': 'N/A',
                        'device_uid': 'placeholder'
                    },
                    'timestamp': current_time.isoformat(),
                }
                scans.append(placeholder_scan)
            
            return scans
        except Exception as e:
            logger.error(f"Error retrieving scans: {e}", exc_info=True)
            # Return placeholder data on error
            return [{
                'device_info': {
                    'model': 'Error Retrieving Scans',
                    'browser': 'N/A',
                    'platform': 'N/A',
                    'device_uid': 'error'
                },
                'timestamp': datetime.now().isoformat(),
            }]

# Initialize managers
receipt_manager = ReceiptManager()
transaction_manager = TransactionManager()
scan_manager = ScanManager()

# Flask routes
@app.route('/')
def home():
    return render_template('index.html')

@app.route('/receipt/<receipt_id>', methods=['GET'])
def get_receipt(receipt_id):
    try:
        receipt = receipt_manager.get_receipt(receipt_id)
        if receipt:
            # Verify validation token if provided
            token = request.args.get('token') or receipt.get('validation_token')
            
            # Format the timestamp if it exists
            if 'timestamp' in receipt:
                try:
                    # Convert timestamp to more readable format
                    dt = datetime.fromisoformat(receipt['timestamp'].replace('Z', '+00:00'))
                    receipt['timestamp'] = dt.strftime('%B %d, %Y %I:%M %p')
                except:
                    pass  # Keep original timestamp if parsing fails
            
            # Ensure all prices are properly formatted
            if 'items' in receipt:
                for item in receipt['items']:
                    if 'price' in item:
                        # Ensure price is float
                        item['price'] = float(item['price'])
            
            if 'total' in receipt:
                receipt['total'] = float(receipt['total'])
            
            # Get scan history
            scans = scan_manager.get_scans(receipt_id)
            logger.debug(f"Scans retrieved for receipt {receipt_id}: {scans}")
            
            # Format scan timestamps
            for scan in scans:
                if 'timestamp' in scan:
                    try:
                        dt = datetime.fromisoformat(scan['timestamp'].replace('Z', '+00:00'))
                        scan['timestamp'] = dt.strftime('%B %d, %Y %I:%M %p')
                    except:
                        pass
                logger.debug(f"Formatted scan: {scan}")
            
            # Generate the QR code URL
            qr_code_url = f"/receipt_qr_{receipt_id}.png"
            
            logger.debug(f"Rendering template with scans: {scans}")
            return render_template('receipt.html', 
                                 receipt=receipt, 
                                 receipt_id=receipt_id,
                                 qr_code_url=qr_code_url,
                                 scans=scans,
                                 token=token,
                                 debug=app.debug)
        
        return jsonify({"error": "Receipt not found"}), 404
    except Exception as e:
        logger.error(f"Error displaying receipt: {e}", exc_info=True)
        return jsonify({"error": "Error displaying receipt"}), 500

@app.route('/receipt', methods=['POST'])
def create_receipt():
    try:
        logger.debug("Received POST request to /receipt")
        receipt_data = request.json
        logger.debug(f"Receipt data: {receipt_data}")
        
        logger.debug("Extracting payment data...")
        payment_data = receipt_data.pop('payment', {})
        
        logger.debug("Creating receipt...")
        receipt_id = receipt_manager.upload_receipt(receipt_data)
        logger.info(f"Generated receipt ID: {receipt_id}")
        
        if receipt_id:
            logger.debug("Generating QR code...")
            qr_success = receipt_manager.generate_qr(receipt_id)
            logger.debug(f"QR generation {'successful' if qr_success else 'failed'}")
            
            logger.debug("Creating transaction...")
            transaction_id = transaction_manager.create_transaction(receipt_id, payment_data)
            logger.info(f"Generated transaction ID: {transaction_id}")
            
            logger.debug("Running garbage collection...")
            gc.collect()
            
            logger.debug("Sending response...")
            return jsonify({
                "receipt_id": receipt_id,
                "transaction_id": transaction_id
            })
        
        logger.error("Failed to create receipt")
        return jsonify({"error": "Failed to create receipt"}), 500
    except Exception as e:
        logger.error(f"Error in create_receipt: {e}", exc_info=True)
        gc.collect()
        return jsonify({"error": str(e)}), 500

@app.route('/receipt_qr_<receipt_id>.png')
def serve_qr(receipt_id):
    qr_path = os.path.join(UPLOAD_FOLDER, f"receipt_qr_{receipt_id}.png")
    if not os.path.exists(qr_path):
        print(f"QR code not found at: {qr_path}")
        return "QR code not found", 404
    return send_file(qr_path, mimetype='image/png')

@app.route('/create')
def create_receipt_page():
    return render_template('receipt_creator.html')

@app.route('/business/transactions')
def transaction_lookup_page():
    return render_template('transaction_lookup.html')

@app.route('/api/transaction/<transaction_id>')
def get_transaction(transaction_id):
    transaction = transaction_manager.get_transaction(transaction_id)
    if transaction:
        # Also get the associated receipt
        receipt = receipt_manager.get_receipt(transaction['receipt_id'])
        if receipt:
            transaction['receipt'] = receipt
        return jsonify(transaction)
    return jsonify({"error": "Transaction not found"}), 404

@app.route('/api/scan/<receipt_id>', methods=['POST'])
def record_scan(receipt_id):
    try:
        data = request.json
        logger.debug(f"Received scan data: {data}")
        
        # Get device_uid from request or generate new one
        device_uid = data.get('device_uid')
        if not device_uid:
            device_uid = str(uuid.uuid4())
            logger.info(f"Generated new device_uid: {device_uid}")
        
        token = data.get('token')
        
        # Verify the receipt exists
        receipt = receipt_manager.get_receipt(receipt_id)
        if not receipt:
            logger.error(f"Receipt not found: {receipt_id}")
            return jsonify({"error": "Receipt not found"}), 404
        
        # Record the scan
        success = scan_manager.record_scan(receipt_id, device_uid, token)
        
        if success:
            return jsonify({
                "success": True,
                "device_uid": device_uid  # Return device_uid so client can store it
            })
        else:
            return jsonify({"error": "Failed to record scan"}), 500
            
    except Exception as e:
        logger.error(f"Error recording scan: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

@app.route('/api/receipt/<receipt_id>')
def get_receipt_json(receipt_id):
    receipt = receipt_manager.get_receipt(receipt_id)
    if receipt:
        return jsonify(receipt)
    return jsonify({"error": "Receipt not found"}), 404

@app.route('/api/receipt/<receipt_id>/transaction')
def get_receipt_transaction(receipt_id):
    try:
        # Find transaction by receipt_id
        transactions = db.collection('transactions')\
            .where('receipt_id', '==', receipt_id)\
            .limit(1)\
            .get()
        
        for doc in transactions:
            transaction = doc.to_dict()
            # Get associated receipt
            receipt = receipt_manager.get_receipt(receipt_id)
            if receipt:
                transaction['receipt'] = receipt
            
            # Get associated scans with device info
            scans = scan_manager.get_scans(receipt_id)
            # Format timestamps and ensure device IDs are included
            for scan in scans:
                if 'timestamp' in scan:
                    try:
                        dt = datetime.fromisoformat(scan['timestamp'].replace('Z', '+00:00'))
                        scan['timestamp'] = dt.strftime('%B %d, %Y %I:%M %p')
                    except:
                        pass
                # Ensure device_uid is present
                if 'device_uid' not in scan and 'device_info' in scan:
                    scan['device_uid'] = scan['device_info'].get('device_uid', 'unknown')
            
            transaction['scans'] = scans
            return jsonify(transaction)
        
        return jsonify({"error": "Transaction not found"}), 404
    except Exception as e:
        logger.error(f"Error getting transaction: {e}", exc_info=True)
        return jsonify({"error": "Error retrieving transaction details"}), 500

@app.route('/generate-pdf/<receipt_id>')
def generate_pdf(receipt_id):
    receipt = receipt_manager.get_receipt(receipt_id)
    if not receipt:
        return jsonify({"error": "Receipt not found"}), 404

    # Create PDF
    buffer = BytesIO()
    p = canvas.Canvas(buffer, pagesize=letter)
    
    # Add receipt content
    y = 750  # Starting y position
    p.setFont("Helvetica-Bold", 18)
    p.drawString(50, y, receipt['store'])
    
    y -= 30
    p.setFont("Helvetica", 12)
    p.drawString(50, y, f"Date: {receipt['timestamp']}")
    
    y -= 30
    p.drawString(50, y, f"Receipt ID: {receipt_id}")
    
    y -= 40
    p.drawString(50, y, "Items:")
    y -= 20
    
    for item in receipt['items']:
        p.drawString(70, y, f"{item['name']}: ${item['price']}")
        y -= 20
    
    y -= 20
    p.setFont("Helvetica-Bold", 12)
    p.drawString(50, y, f"Total: ${receipt['total']}")
    
    p.save()
    
    # Prepare response
    buffer.seek(0)
    return send_file(
        buffer,
        as_attachment=True,
        download_name=f"receipt-{receipt_id}.pdf",
        mimetype='application/pdf'
    )

@app.route('/api/receipt/<receipt_id>/scans')
def get_receipt_scans(receipt_id):
    try:
        scans = scan_manager.get_scans(receipt_id)
        # Format timestamps
        for scan in scans:
            if 'timestamp' in scan:
                try:
                    dt = datetime.fromisoformat(scan['timestamp'].replace('Z', '+00:00'))
                    scan['timestamp'] = dt.strftime('%B %d, %Y %I:%M %p')
                except:
                    pass
        return jsonify(scans)
    except Exception as e:
        logger.error(f"Error getting scans: {e}", exc_info=True)
        return jsonify([])

@app.route('/api/debug/scans/<receipt_id>')
def debug_scans(receipt_id):
    """Debug endpoint to check raw scan data"""
    try:
        # Get raw scan documents
        scans_ref = db.collection('receipts')\
            .document(receipt_id)\
            .collection('scans')\
            .stream()
        
        scans = []
        for scan in scans_ref:
            scan_data = scan.to_dict()
            scan_data['scan_id'] = scan.id  # Include the document ID
            scans.append(scan_data)
        
        return jsonify({
            'receipt_id': receipt_id,
            'scan_count': len(scans),
            'scans': scans
        })
    except Exception as e:
        return jsonify({
            'error': str(e),
            'receipt_id': receipt_id
        }), 500

if __name__ == '__main__':
    app.run(debug=True, port=8080, host='0.0.0.0')
