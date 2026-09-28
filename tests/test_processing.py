import tempfile
import unittest
from pathlib import Path
from pypdf import PdfReader, PdfWriter
import io
from test_api import pdf
from hr_policy.processing import normalize_text, process_pdf, PDFProcessingError
from hr_policy.engine import ingest

class ProcessingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'policy.pdf'

    def test_normalization(self):
        self.assertEqual(normalize_text('Policy\u00a0  text\r\n\x00oﬃce'), 'Policy text\noffice')

    def test_page_numbers_and_blank_warning(self):
        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        writer.add_page(PdfReader(io.BytesIO(pdf())).pages[0])
        writer.write(self.path)
        report = process_pdf(self.path)
        self.assertEqual(report.page_count, 2)
        self.assertEqual(report.pages[0].word_count, 0)
        self.assertEqual(report.pages[1].number, 2)
        self.assertTrue(report.warnings)
        chunks = ingest(self.tmp.name, Path(self.tmp.name) / 'index.json')
        self.assertTrue(all(chunk.page == 2 for chunk in chunks))

    def test_encrypted(self):
        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        writer.encrypt('password')
        writer.write(self.path)
        with self.assertRaisesRegex(PDFProcessingError, 'Encrypted'):
            process_pdf(self.path)

    def test_malformed(self):
        self.path.write_bytes(b'not a PDF')
        with self.assertRaisesRegex(PDFProcessingError, 'not a PDF'):
            process_pdf(self.path)

    def test_page_limit(self):
        writer = PdfWriter()
        for _ in range(2):
            writer.add_page(PdfReader(io.BytesIO(pdf())).pages[0])
        writer.write(self.path)
        with self.assertRaisesRegex(PDFProcessingError, 'page limit'):
            process_pdf(self.path, max_pages=1)

    def test_repeated_single_line_is_not_erased(self):
        writer = PdfWriter()
        for _ in range(2):
            writer.add_page(PdfReader(io.BytesIO(pdf())).pages[0])
        writer.write(self.path)
        report = process_pdf(self.path)
        self.assertTrue(all(page.word_count > 0 for page in report.pages))
