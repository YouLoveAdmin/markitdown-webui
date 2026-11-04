from flask import Flask, jsonify, render_template, request, send_from_directory
import os
from markitdown import MarkItDown
from pypdf import PdfReader
from werkzeug.utils import secure_filename
import uuid
from datetime import datetime
from werkzeug.exceptions import NotFound, RequestEntityTooLarge

# Check if running on Vercel
is_vercel = os.environ.get('VERCEL') == '1'

app = Flask(__name__, 
           template_folder='templates',
           static_folder='static')

app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB max file size

app.config['UPLOAD_FOLDER'] = os.environ.get(
    'UPLOAD_FOLDER', '/tmp/uploads' if is_vercel else 'uploads'
)

# Ensure upload folder exists
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Allowed file extensions
ALLOWED_EXTENSIONS = {
    'pdf', 'docx', 'doc', 'pptx', 'ppt', 'xlsx', 'xls',
    'html', 'htm', 'csv', 'json', 'xml', 'txt', 'md'
}


@app.errorhandler(RequestEntityTooLarge)
def handle_file_too_large(_error):
    return jsonify({'error': 'File exceeds the 50MB size limit'}), 413


@app.route('/healthz')
def health_check():
    return jsonify({'status': 'ok'})


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def get_file_type(filename):
    # Simple file type detection based on extension
    ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else ''
    type_map = {
        'pdf': 'application/pdf',
        'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        'doc': 'application/msword',
        'pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
        'ppt': 'application/vnd.ms-powerpoint',
        'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        'xls': 'application/vnd.ms-excel',
        'html': 'text/html',
        'htm': 'text/html',
        'csv': 'text/csv',
        'json': 'application/json',
        'xml': 'application/xml',
        'txt': 'text/plain',
        'md': 'text/markdown'
    }
    return type_map.get(ext, 'application/octet-stream')

def markdown_table_cell(value):
    return (
        str(value)
        .replace('\\', '\\\\')
        .replace('|', r'\|')
        .replace('\r\n', '<br>')
        .replace('\r', '<br>')
        .replace('\n', '<br>')
    )

def append_pdf_form_fields(markdown_content, file_path):
    with open(file_path, 'rb') as pdf_file:
        fields = PdfReader(pdf_file).get_fields()
        if not fields:
            return markdown_content

        populated_fields = [
            (name, field.get('/V'))
            for name, field in fields.items()
            if field.get('/V') is not None and str(field.get('/V')).strip()
        ]
    if not populated_fields:
        return markdown_content

    rows = [
        f"| {markdown_table_cell(name)} | {markdown_table_cell(value)} |"
        for name, value in populated_fields
    ]
    return (
        f"{markdown_content.rstrip()}\n\n"
        "## PDF Form Fields\n\n"
        "| Field Name | Field Value |\n"
        "|---|---|\n"
        + '\n'.join(rows)
    )

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/convert', methods=['POST'])
def convert_file():
    file_path = None
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'No file provided'}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({'error': 'No file selected'}), 400
        
        filename = secure_filename(file.filename)
        if not filename:
            return jsonify({'error': 'Invalid file name'}), 400

        if not allowed_file(filename):
            return jsonify({'error': 'File type not supported'}), 400
        
        # Generate unique filename
        unique_filename = f"{uuid.uuid4()}_{filename}"
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
        file.save(file_path)
        if os.path.getsize(file_path) == 0:
            return jsonify({'error': 'File is empty'}), 400

        md = MarkItDown(enable_plugins=False)
        result = md.convert(file_path)
        converted_content = result.text_content
        if filename.lower().endswith('.pdf'):
            converted_content = append_pdf_form_fields(converted_content, file_path)

        # Generate output filename
        base_name = os.path.splitext(filename)[0]
        output_filename = f"{base_name}_converted_{uuid.uuid4().hex[:8]}.md"
        output_path = os.path.join(app.config['UPLOAD_FOLDER'], output_filename)
        
        # Save converted content
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(converted_content)
        
        return jsonify({
            'success': True,
            'output_filename': output_filename,
            'download_url': f'/download/{output_filename}',
            'content_preview': converted_content[:500] + '...' if len(converted_content) > 500 else converted_content,
            'file_type': get_file_type(filename)
        })
        
    except RequestEntityTooLarge:
        return jsonify({'error': 'File exceeds the 50MB size limit'}), 413
    except Exception:
        app.logger.exception('File conversion failed')
        return jsonify({'error': 'Conversion failed'}), 500
    finally:
        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
            except OSError:
                app.logger.exception('Failed to remove uploaded file')

@app.route('/download/<filename>')
def download_file(filename):
    try:
        return send_from_directory(
            app.config['UPLOAD_FOLDER'],
            filename,
            as_attachment=True,
            download_name=filename
        )
    except NotFound:
        return jsonify({'error': 'File not found'}), 404
    except OSError:
        app.logger.exception('Failed to download converted file')
        return jsonify({'error': 'Download failed'}), 500

@app.route('/cleanup', methods=['POST'])
def cleanup_files():
    try:
        # Clean up files older than 1 hour
        current_time = datetime.now()
        for filename in os.listdir(app.config['UPLOAD_FOLDER']):
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            if os.path.isfile(file_path):
                file_time = datetime.fromtimestamp(os.path.getmtime(file_path))
                if (current_time - file_time).total_seconds() > 3600:
                    os.remove(file_path)
        return jsonify({'success': True})
    except Exception:
        app.logger.exception('File cleanup failed')
        return jsonify({'error': 'Cleanup failed'}), 500

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5001)
