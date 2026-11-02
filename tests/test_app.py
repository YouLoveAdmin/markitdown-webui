import io
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app import app


class AppApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.upload_dir = tempfile.TemporaryDirectory()
        cls.original_upload_folder = app.config['UPLOAD_FOLDER']
        app.config['UPLOAD_FOLDER'] = cls.upload_dir.name

    @classmethod
    def tearDownClass(cls):
        app.config['UPLOAD_FOLDER'] = cls.original_upload_folder
        cls.upload_dir.cleanup()

    def setUp(self):
        self.client = app.test_client()
        self.original_max_content_length = app.config['MAX_CONTENT_LENGTH']

    def tearDown(self):
        app.config['MAX_CONTENT_LENGTH'] = self.original_max_content_length

    def test_health_endpoint_returns_json(self):
        response = self.client.get('/healthz')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {'status': 'ok'})

    def test_convert_rejects_missing_file(self):
        response = self.client.post('/convert')

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json(), {'error': 'No file provided'})

    def test_convert_rejects_empty_file(self):
        response = self.client.post(
            '/convert',
            data={'file': (io.BytesIO(b''), 'empty.txt')},
            content_type='multipart/form-data',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json(), {'error': 'File is empty'})

    def test_convert_rejects_unsupported_file(self):
        response = self.client.post(
            '/convert',
            data={'file': (io.BytesIO(b'data'), 'input.exe')},
            content_type='multipart/form-data',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json(), {'error': 'File type not supported'})

    def test_convert_rejects_oversized_file_as_json(self):
        app.config['MAX_CONTENT_LENGTH'] = 4
        response = self.client.post(
            '/convert',
            data={'file': (io.BytesIO(b'12345'), 'input.txt')},
            content_type='multipart/form-data',
        )

        self.assertEqual(response.status_code, 413)
        self.assertEqual(
            response.get_json(),
            {'error': 'File exceeds the 50MB size limit'},
        )

    @patch('app.MarkItDown')
    def test_convert_returns_unique_downloadable_output(self, markitdown):
        markitdown.return_value.convert.return_value = SimpleNamespace(
            text_content='Converted content'
        )

        response = self.client.post(
            '/convert',
            data={'file': (io.BytesIO(b'input'), 'notes.txt')},
            content_type='multipart/form-data',
        )

        self.assertEqual(response.status_code, 200)
        result = response.get_json()
        self.assertTrue(result['success'])
        self.assertEqual(result['content_preview'], 'Converted content')
        self.assertEqual(result['file_type'], 'text/plain')
        self.assertRegex(result['output_filename'], r'^notes_converted_[0-9a-f]{8}\.md$')
        download_response = self.client.get(result['download_url'])
        self.assertEqual(download_response.data, b'Converted content')
        download_response.close()
        self.assertEqual(
            [name for name in os.listdir(self.upload_dir.name) if name.endswith('.txt')],
            [],
        )

    @patch('app.MarkItDown')
    def test_convert_hides_internal_conversion_errors(self, markitdown):
        markitdown.return_value.convert.side_effect = RuntimeError('private detail')

        response = self.client.post(
            '/convert',
            data={'file': (io.BytesIO(b'input'), 'notes.txt')},
            content_type='multipart/form-data',
        )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.get_json(), {'error': 'Conversion failed'})

    def test_download_rejects_missing_file(self):
        response = self.client.get('/download/missing.md')

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json(), {'error': 'File not found'})


if __name__ == '__main__':
    unittest.main()
