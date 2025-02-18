import firebase_admin
from firebase_admin import credentials, firestore
import qrcode
from flask import Flask, jsonify, request, render_template_string, send_file, render_template, make_response
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
from dotenv import load_dotenv
from firebase_config import initialize_firebase

# Load environment variables from .env file
load_dotenv()

# Set up logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Initialize Flask app with custom template folder
template_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'Gaia', 'templates')
app = Flask(__name__, 
           template_folder=template_dir,
           static_folder=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'Gaia', 'static'))

# Update UPLOAD_FOLDER path and ensure it exists
UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'qrcodes')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

logger.debug(f"Upload folder path: {UPLOAD_FOLDER}")

# Initialize Firebase
firebase_app = initialize_firebase()
db = firestore.client()
logger.info("Firebase initialized successfully")

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
            
            # For local development, use local IP address so phones on the same network can access
            if not base_url:
                # Get local IP address that's accessible on the network
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                try:
                    # Doesn't need to be reachable
                    s.connect(('10.255.255.255', 1))
                    local_ip = s.getsockname()[0]
                except Exception:
                    local_ip = '127.0.0.1'
                finally:
                    s.close()
                base_url = f"http://{local_ip}:8080"
                logger.debug(f"Using local IP for development: {base_url}")
            
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
                box_size=10,
                border=4
            )
            qr.add_data(url)
            qr.make(fit=True)
            
            # Create the QR code image
            qr_path = os.path.join(UPLOAD_FOLDER, f"receipt_qr_{receipt_id}.png")
            logger.debug(f"Saving QR code to: {qr_path}")
            
            # Ensure the directory exists
            os.makedirs(os.path.dirname(qr_path), exist_ok=True)
            
            # Save the QR code with higher resolution
            img = qr.make_image(fill_color="black", back_color="white")
            img = img.resize((300, 300))  # Make QR code larger
            img.save(qr_path, quality=95)  # Save with high quality
            
            logger.debug(f"QR code saved successfully at {qr_path}")
            return True
            
        except Exception as e:
            logger.error(f"Error generating QR code: {e}", exc_info=True)
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
    """Show the landing page"""
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
            if not qr_success:
                logger.error("Failed to generate QR code")
                return jsonify({"error": "Failed to generate QR code"}), 500
            logger.debug("QR code generated successfully")
            
            logger.debug("Creating transaction...")
            transaction_id = transaction_manager.create_transaction(receipt_id, payment_data)
            logger.info(f"Generated transaction ID: {transaction_id}")
            
            return jsonify({
                "receipt_id": receipt_id,
                "transaction_id": transaction_id
            })
        
        logger.error("Failed to create receipt")
        return jsonify({"error": "Failed to create receipt"}), 500
    except Exception as e:
        logger.error(f"Error in create_receipt: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

@app.route('/receipt_qr_<receipt_id>.png')
def serve_qr(receipt_id):
    """Serve QR code image"""
    try:
        qr_path = os.path.join(UPLOAD_FOLDER, f"receipt_qr_{receipt_id}.png")
        logger.debug(f"Attempting to serve QR code from: {qr_path}")
        
        if not os.path.exists(qr_path):
            logger.error(f"QR code not found at: {qr_path}")
            # Generate QR code if it doesn't exist
            receipt = receipt_manager.get_receipt(receipt_id)
            if receipt:
                receipt_manager.generate_qr(receipt_id)
                if os.path.exists(qr_path):
                    return send_file(qr_path, mimetype='image/png')
            return "QR code not found", 404
            
        logger.debug(f"Successfully found QR code at: {qr_path}")
        return send_file(qr_path, mimetype='image/png')
        
    except Exception as e:
        logger.error(f"Error serving QR code: {e}", exc_info=True)
        return "Error serving QR code", 500

@app.route('/create')
def create_receipt_page():
    return render_template('receipt_creator.html')

@app.route('/business/transactions')
def transaction_lookup_page():
    return render_template('transaction_lookup.html')

@app.route('/api/transaction/<transaction_id>')
def get_transaction(transaction_id):
    try:
        transaction = transaction_manager.get_transaction(transaction_id)
        if transaction:
            # Get the associated receipt
            receipt = receipt_manager.get_receipt(transaction['receipt_id'])
            if receipt:
                # Include both receipt and receipt_id in the response
                return jsonify({
                    **transaction,
                    'receipt': receipt,
                    'receipt_id': transaction['receipt_id']  # Explicitly include receipt_id
                })
            return jsonify({"error": "Receipt not found"}), 404
        return jsonify({"error": "Transaction not found"}), 404
    except Exception as e:
        logger.error(f"Error getting transaction: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

@app.route('/api/scan/<receipt_id>', methods=['POST'])
def record_scan(receipt_id):
    try:
        data = request.json
        logger.debug(f"Scan endpoint called for receipt {receipt_id}")
        logger.debug(f"Request headers: {dict(request.headers)}")
        logger.debug(f"Received scan data: {data}")
        
        # Get device_uid from request or generate new one
        device_uid = data.get('device_uid')
        logger.debug(f"Initial device_uid: {device_uid}")
        
        if not device_uid:
            device_uid = str(uuid.uuid4())
            logger.info(f"Generated new device_uid: {device_uid}")
        
        token = data.get('token')
        logger.debug(f"Token received: {token}")
        
        # Verify the receipt exists
        receipt = receipt_manager.get_receipt(receipt_id)
        logger.debug(f"Receipt found: {bool(receipt)}")
        
        if not receipt:
            logger.error(f"Receipt not found: {receipt_id}")
            return jsonify({"error": "Receipt not found"}), 404
        
        # Record the scan
        logger.debug("Attempting to record scan...")
        success = scan_manager.record_scan(receipt_id, device_uid, token)
        logger.debug(f"Scan recording success: {success}")
        
        if success:
            logger.debug("Returning success response")
            return jsonify({
                "success": True,
                "device_uid": device_uid
            })
        else:
            logger.debug("Returning failure response")
            return jsonify({"error": "Failed to record scan"}), 500
            
    except Exception as e:
        logger.error(f"Error recording scan: {e}", exc_info=True)
        logger.debug("Stack trace:", exc_info=True)
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

@app.route('/api/stats/scans/<receipt_id>')
def get_scan_stats(receipt_id):
    """Get detailed scan statistics for a receipt"""
    try:
        # Get all scans for the receipt
        scan_refs = db.collection('receipts')\
            .document(receipt_id)\
            .collection('scans')\
            .order_by('timestamp', direction=firestore.Query.DESCENDING)\
            .stream()
        
        # Collect scan data
        scans = []
        unique_devices = set()
        
        for scan in scan_refs:
            scan_data = scan.to_dict()
            device_uid = scan_data.get('device_uid', 'unknown')
            unique_devices.add(device_uid)
            
            # Add formatted scan info
            scans.append({
                'timestamp': scan_data.get('timestamp'),
                'device_uid': device_uid,
                'device_info': {
                    'model': scan_data.get('device_info', {}).get('model', 'Unknown'),
                    'platform': scan_data.get('device_info', {}).get('platform', 'Unknown'),
                    'browser': scan_data.get('device_info', {}).get('browser', 'Unknown')
                }
            })
        
        stats = {
            'receipt_id': receipt_id,
            'total_scans': len(scans),
            'unique_devices': len(unique_devices),
            'scans_by_device': {},
            'all_scans': scans
        }
        
        # Count scans per device
        for scan in scans:
            device = scan['device_uid']
            if device not in stats['scans_by_device']:
                stats['scans_by_device'][device] = {
                    'count': 0,
                    'last_scan': None,
                    'device_info': scan['device_info']
                }
            stats['scans_by_device'][device]['count'] += 1
            if not stats['scans_by_device'][device]['last_scan']:
                stats['scans_by_device'][device]['last_scan'] = scan['timestamp']
        
        return jsonify(stats)
        
    except Exception as e:
        logger.error(f"Error getting scan stats: {e}", exc_info=True)
        return jsonify({
            'error': str(e),
            'receipt_id': receipt_id
        }), 500

@app.route('/receipt/<receipt_id>/stats')
def view_receipt_stats(receipt_id):
    try:
        # Get receipt details
        receipt = receipt_manager.get_receipt(receipt_id)
        if not receipt:
            return jsonify({"error": "Receipt not found"}), 404
            
        # Get scan stats
        stats = get_scan_stats(receipt_id).get_json()
        
        return render_template('receipt_stats.html',
                             receipt=receipt,
                             stats=stats)
    except Exception as e:
        logger.error(f"Error viewing stats: {e}", exc_info=True)
        return jsonify({"error": "Error viewing stats"}), 500

@app.route('/receipt/<receipt_id>')
def view_receipt(receipt_id):
    """Public route for viewing receipts via QR code"""
    try:
        # Get the validation token from query params
        token = request.args.get('token')
        
        if not token:
            return render_template('error.html', message="Invalid receipt link"), 400
        
        # Get receipt details
        receipt = receipt_manager.get_receipt(receipt_id)
        
        if not receipt:
            return render_template('error.html', message="Receipt not found"), 404
            
        # Verify the token matches
        if receipt.get('validation_token') != token:
            return render_template('error.html', message="Invalid receipt token"), 401
        
        # Record the scan
        device_uid = request.cookies.get('device_uid', str(uuid.uuid4()))
        scan_manager.record_scan(receipt_id, device_uid, token)
        
        # Render the receipt view template
        response = make_response(render_template('receipt_view.html', receipt=receipt))
        response.set_cookie('device_uid', device_uid, max_age=31536000)  # 1 year
        return response
        
    except Exception as e:
        logger.error(f"Error viewing receipt: {e}", exc_info=True)
        return render_template('error.html', message="Error viewing receipt"), 500

@app.route('/demo')
def demo_page():
    """Show the customer demo page"""
    return render_template('customer/demo.html')

@app.route('/how-it-works')
def how_it_works():
    """Show the how it works page"""
    return render_template('how_it_works.html')

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    # Use 0.0.0.0 to allow external connections
    app.run(host='0.0.0.0', port=port, debug=True)
