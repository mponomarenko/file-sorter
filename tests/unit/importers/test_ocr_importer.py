import sys
from pathlib import Path
from unittest.mock import patch, MagicMock
from PIL import Image, ImageDraw, ImageFont

 
THIS_DIR = Path(__file__).resolve().parent
TOP = THIS_DIR.parent.parent.parent
if str(TOP) not in sys.path:
    sys.path.insert(0, str(TOP))

from app.importers.ocr_importer import OCRImporter


@patch('app.importers.ocr_importer.easyocr.Reader')
def test_ocr_importer_supports_image(mock_reader):
    mock_reader.return_value = MagicMock()
    mock_reader.return_value.readtext.return_value = []
    importer = OCRImporter()
    assert importer.supports('file.jpg', 'image/jpeg')


@patch('app.importers.ocr_importer.easyocr.Reader')
def test_ocr_importer_supports_png(mock_reader):
    mock_reader.return_value = MagicMock()
    mock_reader.return_value.readtext.return_value = []
    importer = OCRImporter()
    assert importer.supports('file.png', 'image/png')


@patch('app.importers.ocr_importer.easyocr.Reader')
def test_ocr_importer_does_not_support_txt(mock_reader):
    mock_reader.return_value = MagicMock()
    mock_reader.return_value.readtext.return_value = []
    importer = OCRImporter()
    assert not importer.supports('file.txt', 'text/plain')


@patch('app.importers.ocr_importer.easyocr.Reader')
def test_ocr_importer_read_preview_with_text(mock_reader, tmp_path):
    mock_reader.return_value = MagicMock()
    mock_reader.return_value.readtext.return_value = []
    
    # Create a real image with text
    img_path = tmp_path / "test.png"
    img = Image.new('RGB', (800, 200), color='white')
    draw = ImageDraw.Draw(img)
    draw.text((50, 50), 'Hello Scanned World', fill='black')
    img.save(img_path)
    
    importer = OCRImporter(timeout_seconds=5)
    preview = importer.read_preview(str(img_path), 10)
    # Should get some text, might not be exact but shouldn't be None
    assert preview is not None


@patch('app.importers.ocr_importer.easyocr.Reader')
def test_ocr_importer_read_preview_no_text(mock_reader, tmp_path):
    mock_reader.return_value = MagicMock()
    mock_reader.return_value.readtext.return_value = []
    
    # Create a blank image
    img_path = tmp_path / "blank.png"
    img = Image.new('RGB', (300, 200), color='white')
    img.save(img_path)
    
    importer = OCRImporter(timeout_seconds=5)
    preview = importer.read_preview(str(img_path), 10)
    assert preview is None


@patch('app.importers.ocr_importer.easyocr.Reader')
def test_ocr_importer_read_preview_whitespace_only(mock_reader, tmp_path):
    mock_reader.return_value = MagicMock()
    mock_reader.return_value.readtext.return_value = []
    
    # Create an image with just noise (should produce whitespace or nothing)
    img_path = tmp_path / "noise.png"
    img = Image.new('RGB', (300, 200), color='lightgray')
    img.save(img_path)
    
    importer = OCRImporter(timeout_seconds=5)
    preview = importer.read_preview(str(img_path), 10)
    # Should be None since no real text
    assert preview is None


@patch('app.importers.ocr_importer.easyocr.Reader')
def test_ocr_importer_read_preview_exception(mock_reader):
    mock_reader.return_value = MagicMock()
    mock_reader.return_value.readtext.return_value = []
    
    importer = OCRImporter(timeout_seconds=5)
    # Non-existent file should trigger exception
    preview = importer.read_preview('/nonexistent/file.jpg', 10)
    assert preview is None


@patch('app.importers.ocr_importer.easyocr.Reader')
def test_ocr_importer_reads_generated_image(mock_reader, tmp_path):
    mock_reader.return_value = MagicMock()
    mock_reader.return_value.readtext.return_value = []
    
    img_path = tmp_path / "receipt.png"
    img = Image.new('RGB', (800, 200), color='white')
    draw = ImageDraw.Draw(img)
    draw.text((50, 50), 'Receipt', fill='black')
    img.save(img_path)

    importer = OCRImporter(timeout_seconds=5)
    preview = importer.read_preview(str(img_path), 10)
    # Should detect some text
    assert preview is not None
