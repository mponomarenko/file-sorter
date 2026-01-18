import sys
from pathlib import Path
from io import BytesIO
from PIL import Image, ImageDraw
import zlib

THIS_DIR = Path(__file__).resolve().parent
TOP = THIS_DIR.parent.parent.parent
if str(TOP) not in sys.path:
    sys.path.insert(0, str(TOP))

from app.importers.pdf_importer import PdfImporter
from app.importers.text_importer import TextFileImporter
from app.media import peek_text


def _write_sample_pdf(path: Path, text: str) -> None:
    content = f"BT /F1 18 Tf 50 700 Td ({text}) Tj ET\n"
    content_bytes = content.encode("latin-1")
    objects = [
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\nendobj\n",
        f"4 0 obj\n<< /Length {len(content_bytes)} >>\nstream\n{content}endstream\nendobj\n".encode("latin-1"),
        b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n",
    ]
    header = b"%PDF-1.4\n"
    offsets = []
    body = b""
    position = len(header)
    for obj in objects:
        offsets.append(position)
        body += obj
        position += len(obj)
    xref_start = len(header) + len(body)
    xref_entries = ["0000000000 65535 f \n"]
    for offset in offsets:
        xref_entries.append(f"{offset:010d} 00000 n \n")
    xref = ("xref\n0 {count}\n".format(count=len(offsets) + 1) + "".join(xref_entries)).encode("latin-1")
    trailer = (
        "trailer\n<< /Size {count} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".format(
            count=len(offsets) + 1, xref=xref_start
        ).encode("latin-1")
    )
    path.write_bytes(header + body + xref + trailer)


def test_text_importer_reads_plain_text(tmp_path):
    importer = TextFileImporter()
    text_path = tmp_path / "sample.txt"
    text_path.write_text("hello importer world", encoding="utf-8")

    assert importer.supports(str(text_path), "text/plain") is True
    assert importer.supports(str(text_path), "application/json") is True
    assert importer.supports(str(text_path), "application/octet-stream") is False

    preview = importer.read_preview(str(text_path), 5)
    assert preview == "hello"


def test_pdf_importer_reads_simple_pdf(tmp_path):
    importer = PdfImporter()
    pdf_path = tmp_path / "sample.pdf"
    _write_sample_pdf(pdf_path, "Hello PDF World")

    assert importer.supports(str(pdf_path), "application/pdf") is True
    assert importer.supports(str(pdf_path), "text/plain") is False

    preview = importer.read_preview(str(pdf_path), 64)
    assert preview.strip().startswith("Hello PDF")


def test_peek_text_uses_pdf_importer(tmp_path):
    pdf_path = tmp_path / "peek.pdf"
    _write_sample_pdf(pdf_path, "Peek PDF Text")

    preview = peek_text(str(pdf_path), "application/pdf", 20)
    assert preview.strip().startswith("Peek PDF")


def _write_image_pdf(path: Path, text: str) -> None:
    img = Image.new("L", (50, 50), color=255)
    draw = ImageDraw.Draw(img)
    draw.text((5, 20), text, fill=0)
    raw = img.tobytes()
    compressed = zlib.compress(raw)

    catalog = b"1 0 obj<< /Type /Catalog /Pages 2 0 R >>endobj\n"
    pages = b"2 0 obj<< /Type /Pages /Kids [3 0 R] /Count 1 >>endobj\n"
    page = (
        b"3 0 obj<< /Type /Page /Parent 2 0 R "
        b"/Resources << /ProcSet [/PDF /ImageB] /XObject << /Im0 4 0 R >> >> "
        b"/MediaBox [0 0 200 200] /Contents 5 0 R >>endobj\n"
    )
    image_obj = (
        f"4 0 obj<< /Type /XObject /Subtype /Image /Width {img.width} /Height {img.height} "
        f"/ColorSpace /DeviceGray /BitsPerComponent 8 /Filter /FlateDecode /Length {len(compressed)} >>stream\n".encode()
        + compressed
        + b"\nendstream\nendobj\n"
    )
    content_stream = b"q 200 0 0 200 0 0 cm /Im0 Do Q"
    content_obj = (
        f"5 0 obj<< /Length {len(content_stream)} >>stream\n".encode()
        + content_stream
        + b"\nendstream\nendobj\n"
    )
    body = catalog + pages + page + image_obj + content_obj
    offsets = []
    header_len = len(b"%PDF-1.4\n")
    pos = header_len
    for chunk in [catalog, pages, page, image_obj, content_obj]:
        offsets.append(pos)
        pos += len(chunk)
    xref_start = pos

    xref = ["0000000000 65535 f \n"]
    for off in offsets:
        xref.append(f"{off:010d} 00000 n \n")
    xref_bytes = ("xref\n0 6\n" + "".join(xref)).encode()
    trailer = f"trailer<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref_start}\n%%EOF\n".encode()

    with open(path, "wb") as f:
        f.write(b"%PDF-1.4\n")
        f.write(body)
        f.write(xref_bytes)
        f.write(trailer)


def test_pdf_importer_falls_back_to_ocr(monkeypatch, tmp_path):
    importer = PdfImporter()
    pdf_path = tmp_path / "scan.pdf"
    _write_image_pdf(pdf_path, "OCR")

    # Avoid invoking external binary; just ensure OCR path populates text.
    monkeypatch.setattr(
        "app.importers.pdf_importer.pytesseract",
        type("Stub", (), {"image_to_string": staticmethod(lambda *_: "OCR Fallback")})(),
    )

    preview = importer.read_preview(str(pdf_path), 64)
    assert preview.startswith("OCR Fallback")


def test_pdf_importer_none_when_no_text_or_images(tmp_path):
    importer = PdfImporter()
    pdf_path = tmp_path / "blank.pdf"
    _write_sample_pdf(pdf_path, "")
    assert importer.read_preview(str(pdf_path), 64) is None
