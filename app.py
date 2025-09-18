# app.py
import os
import re
import csv
from flask import Flask, request, jsonify, send_from_directory
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

# helper: extract text from PDF or simple text files
def extract_text_from_file(filepath, filename):
    ext = filename.lower().split('.')[-1]
    text = ''
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
        # generic binary fallback: try to read as text
        with open(filepath, 'rb') as f:
            raw = f.read()
            try:
                text = raw.decode('utf-8', errors='ignore')
            except Exception:
                text = ''
    return text

# helper: simple key-value pair extractor from text
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
            # some common headings to capture
            headings = [
                'name of the claimant', 'name of the claimant(s)', 'name of the spouse',
                'name of father', 'address', 'village', 'gram panchayat', 'tehsil', 'district',
                'total area claimed', 'survey numbers', 'gps coordinates', 'date of submission',
                'tribe', 'family members', 'signature'
            ]
            for h in headings:
                if h in lower:
                    # next non-empty line is likely the value
                    for j in range(i+1, min(i+6, len(lines))):
                        cand = lines[j]
                        if len(cand) > 0 and len(cand) < 200:
                            pairs[ln] = cand
                            break

    # Heuristics: if no pairs found, grab lines with keywords
    if not pairs:
        for ln in lines[:200]:
            if re.search(r'\b(age|hectares|survey|total area|gps|date)\b', ln, re.I):
                pairs.setdefault('misc', []).append(ln)
    return pairs

# append to CSV in a flexible manner; we will store one JSON-like column for parsed pairs
def append_to_csv(form_fields, parsed_pairs):
    file_exists = os.path.exists(CSV_FILE)
    with open(CSV_FILE, 'a', newline='', encoding='utf8') as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(['claimant_name','gram_sabha_id','claim_type','parsed_pairs','filename'])
        writer.writerow([form_fields.get('claimant_name',''),
                         form_fields.get('gram_sabha_id',''),
                         form_fields.get('claim_type',''),
                         str(parsed_pairs),
                         form_fields.get('filename','')])

@app.route('/')
def serve_index():
    return send_from_directory('.', 'index1.html')

@app.route('/upload', methods=['POST'])
def upload():
    try:
        claimant_name = request.form.get('claimant_name','')
        gram_sabha_id = request.form.get('gram_sabha_id','')
        claim_type = request.form.get('claim_type','')
        file = request.files.get('claim_file')
        if file is None:
            return jsonify(success=False, error='No file provided'), 400

        filename = secure_filename(file.filename)
        saved_path = os.path.join(UPLOAD_DIR, filename)
        file.save(saved_path)

        text = extract_text_from_file(saved_path, filename)
        parsed = parse_key_values(text)

        # Save to csv
        append_to_csv({'claimant_name':claimant_name, 'gram_sabha_id':gram_sabha_id,
                       'claim_type':claim_type, 'filename':filename}, parsed)

        return jsonify(success=True, parsed_pairs=parsed)
    except Exception as e:
        return jsonify(success=False, error=str(e)), 500
@app.route('/view_database', methods=['GET'])
def view_database():
    if not os.path.exists(CSV_FILE):
        return jsonify(success=False, error='CSV not found')
    try:
        with open(CSV_FILE, 'r', encoding='utf8') as f:
            rows = list(csv.reader(f))
            if len(rows) <= 1:
                return jsonify(success=False, error='No data rows')
            header = rows[0]
            data = [dict(zip(header, r)) for r in rows[1:]]
            return jsonify(success=True, rows=data)
    except Exception as e:
        return jsonify(success=False, error=str(e)), 500

@app.route('/last_entry', methods=['GET'])
def last_entry():
    if not os.path.exists(CSV_FILE):
        return jsonify(success=False, error='CSV not found')
    try:
        with open(CSV_FILE, 'r', encoding='utf8') as f:
            rows = list(csv.reader(f))
            if len(rows) <= 1:
                return jsonify(success=False, error='No data rows')
            header = rows[0]
            last = rows[-1]
            row = dict(zip(header, last))
            return jsonify(success=True, row=row)
    except Exception as e:
        return jsonify(success=False, error=str(e)), 500

if __name__ == '__main__':
    print("Starting Flask on http://127.0.0.1:5000")
    app.run(debug=True)
