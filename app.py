import os
import re
import csv
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from werkzeug.utils import secure_filename

# For PDF text extraction
try:
    import pdfplumber
except Exception:
    pdfplumber = None

UPLOAD_DIR = 'uploads'
CSV_FILE = 'claims_central.csv'
os.makedirs(UPLOAD_DIR, exist_ok=True)

app = Flask(__name__, static_folder='.')
CORS(app, origins=['*'], methods=['GET', 'POST', 'OPTIONS'])

# Configure CORS for all routes
@app.after_request
def after_request(response):
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
    response.headers.add('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE,OPTIONS')
    return response

# Helper: extract text from PDF or simple text files
def extract_text_from_file(filepath, filename):
    ext = filename.lower().split('.')[-1]
    text = ''
    try:
        if ext == 'pdf':
            if pdfplumber is None:
                raise RuntimeError('pdfplumber not installed. Install with: pip install pdfplumber')
            with pdfplumber.open(filepath) as pdf:
                pages = [p.extract_text() or '' for p in pdf.pages]
                text = '\n'.join(pages)
        elif ext in ('txt',):
            with open(filepath, 'r', encoding='utf8', errors='ignore') as f:
                text = f.read()
        elif ext in ('docx',):
            try:
                import docx
                doc = docx.Document(filepath)
                text = '\n'.join([p.text for p in doc.paragraphs])
            except Exception as e:
                raise RuntimeError('docx support needs python-docx. Install with: pip install python-docx') from e
        else:
            # Generic binary fallback: try to read as text
            with open(filepath, 'rb') as f:
                raw = f.read()
                try:
                    text = raw.decode('utf-8', errors='ignore')
                except Exception:
                    text = ''
    except Exception as e:
        print(f"Error extracting text from {filename}: {str(e)}")
        text = f"Error extracting text: {str(e)}"
    
    return text

# Helper: simple key-value pair extractor from text
def parse_key_values(text):
    pairs = {}
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    
    # First pass: look for "Key: Value" or "Key - Value"
    kv_re = re.compile(r'^\s*([A-Za-z0-9\s\/\-\(\)\.]{2,80}?)\s*[:\-]\s*(.+)$')
    for ln in lines:
        m = kv_re.match(ln)
        if m:
            k = m.group(1).strip()
            v = m.group(2).strip()
            pairs[k] = v

    # Second pass: headings style "Name of the claimant(s)" next line "Ravi Kumar Bhuyan"
    for i, ln in enumerate(lines):
        lower = ln.lower()
        if len(ln) < 120 and len(ln) > 3:
            # Common headings to capture
            headings = [
                'name of the claimant', 'name of the claimant(s)', 'name of the spouse',
                'name of father', 'address', 'village', 'gram panchayat', 'tehsil', 'district',
                'total area claimed', 'survey numbers', 'gps coordinates', 'date of submission',
                'tribe', 'family members', 'signature'
            ]
            for h in headings:
                if h in lower:
                    # Next non-empty line is likely the value
                    for j in range(i+1, min(i+6, len(lines))):
                        cand = lines[j]
                        if len(cand) > 0 and len(cand) < 200:
                            pairs[ln] = cand
                            break

    # Heuristics: if no pairs found, grab lines with keywords
    if not pairs:
        misc_lines = []
        for ln in lines[:200]:
            if re.search(r'\b(age|hectares|survey|total area|gps|date)\b', ln, re.I):
                misc_lines.append(ln)
        if misc_lines:
            pairs['misc'] = '; '.join(misc_lines)
    
    return pairs

# Append to CSV in a flexible manner; store parsed pairs as JSON-like column
def append_to_csv(form_fields, parsed_pairs):
    file_exists = os.path.exists(CSV_FILE)
    try:
        with open(CSV_FILE, 'a', newline='', encoding='utf8') as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(['claimant_name','gram_sabha_id','claim_type','parsed_pairs','filename'])
            writer.writerow([
                form_fields.get('claimant_name',''),
                form_fields.get('gram_sabha_id',''),
                form_fields.get('claim_type',''),
                str(parsed_pairs),
                form_fields.get('filename','')
            ])
    except Exception as e:
        print(f"Error writing to CSV: {str(e)}")
        raise

@app.route('/')
def serve_index():
    try:
        return send_from_directory('.', 'index1.html')
    except Exception as e:
        return jsonify(success=False, error=f'File not found: {str(e)}'), 404

@app.route('/health')
def health_check():
    return jsonify(success=True, status='online', message='Server is running'), 200

@app.route('/upload', methods=['POST', 'OPTIONS'])
def upload():
    if request.method == 'OPTIONS':
        return jsonify(success=True), 200
        
    try:
        # Get form data
        claimant_name = request.form.get('claimant_name', '').strip()
        gram_sabha_id = request.form.get('gram_sabha_id', '').strip()
        claim_type = request.form.get('claim_type', '').strip()
        file = request.files.get('claim_file')
        
        # Validate inputs
        if not claimant_name:
            return jsonify(success=False, error='Claimant name is required'), 400
        if not gram_sabha_id:
            return jsonify(success=False, error='Gram Sabha ID is required'), 400
        if not claim_type:
            return jsonify(success=False, error='Claim type is required'), 400
        if file is None or file.filename == '':
            return jsonify(success=False, error='No file provided'), 400

        # Secure the filename
        filename = secure_filename(file.filename)
        if not filename:
            return jsonify(success=False, error='Invalid filename'), 400

        # Check file extension
        allowed_extensions = {'pdf', 'txt', 'docx'}
        file_ext = filename.lower().split('.')[-1] if '.' in filename else ''
        if file_ext not in allowed_extensions:
            return jsonify(success=False, error=f'File type not supported. Allowed: {", ".join(allowed_extensions)}'), 400

        # Save uploaded file
        saved_path = os.path.join(UPLOAD_DIR, filename)
        file.save(saved_path)

        # Extract text and parse
        text = extract_text_from_file(saved_path, filename)
        parsed = parse_key_values(text)

        # Save to CSV
        form_fields = {
            'claimant_name': claimant_name,
            'gram_sabha_id': gram_sabha_id,
            'claim_type': claim_type,
            'filename': filename
        }
        append_to_csv(form_fields, parsed)

        return jsonify(
            success=True,
            message='File uploaded and processed successfully',
            parsed_pairs=parsed,
            filename=filename
        ), 200

    except Exception as e:
        print(f"Upload error: {str(e)}")
        return jsonify(success=False, error=f'Server error: {str(e)}'), 500

@app.route('/view_database', methods=['GET', 'OPTIONS'])
def view_database():
    if request.method == 'OPTIONS':
        return jsonify(success=True), 200
        
    try:
        if not os.path.exists(CSV_FILE):
            return jsonify(success=False, error='No data found. Upload some files first.'), 404
            
        with open(CSV_FILE, 'r', encoding='utf8') as f:
            rows = list(csv.reader(f))
            if len(rows) <= 1:
                return jsonify(success=False, error='No data rows found'), 404
                
            header = rows[0]
            data = []
            for row in rows[1:]:
                if len(row) == len(header):
                    data.append(dict(zip(header, row)))
                    
            return jsonify(success=True, rows=data, count=len(data)), 200
            
    except Exception as e:
        print(f"Database view error: {str(e)}")
        return jsonify(success=False, error=f'Error reading database: {str(e)}'), 500

@app.route('/last_entry', methods=['GET', 'OPTIONS'])
def last_entry():
    if request.method == 'OPTIONS':
        return jsonify(success=True), 200
        
    try:
        if not os.path.exists(CSV_FILE):
            return jsonify(success=False, error='No data found'), 404
            
        with open(CSV_FILE, 'r', encoding='utf8') as f:
            rows = list(csv.reader(f))
            if len(rows) <= 1:
                return jsonify(success=False, error='No data rows found'), 404
                
            header = rows[0]
            last_row = rows[-1]
            if len(last_row) != len(header):
                return jsonify(success=False, error='Data format error'), 500
                
            row_data = dict(zip(header, last_row))
            return jsonify(success=True, row=row_data), 200
            
    except Exception as e:
        print(f"Last entry error: {str(e)}")
        return jsonify(success=False, error=f'Error reading last entry: {str(e)}'), 500

# Error handlers
@app.errorhandler(404)
def not_found(error):
    return jsonify(success=False, error='Resource not found'), 404

@app.errorhandler(500)
def internal_error(error):
    return jsonify(success=False, error='Internal server error'), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug_mode = os.environ.get('FLASK_ENV') == 'development'
    
    print(f"Starting server on port {port}")
    print(f"Upload directory: {UPLOAD_DIR}")
    print(f"CSV file: {CSV_FILE}")
    
    app.run(host='0.0.0.0', port=port, debug=debug_mode)