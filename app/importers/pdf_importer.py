from __future__ import annotations
from __future__ import annotations

from typing import Iterable
from io import BytesIO

from pypdf import PdfReader
from PIL import Image
import pytesseract

from .interface import PeekImporter

_MIN_TEXT_THRESHOLD = 100
_MIN_DIMENSION_FOR_RELIABLE_OCR = 600


def _prepare_image_for_ocr(image: Image.Image) -> Image.Image:
    """Upscale and normalize images so Tesseract keeps leading characters."""
    if not isinstance(image, Image.Image):
        return image
    width, height = image.size
    min_dimension = min(width, height)
    if min_dimension < _MIN_DIMENSION_FOR_RELIABLE_OCR:
        scale = _MIN_DIMENSION_FOR_RELIABLE_OCR / min_dimension
        new_width = int(width * scale)
        new_height = int(height * scale)
        image = image.resize((new_width, new_height), Image.Resampling.LANCZOS)
    return image.convert("L")


class PdfImporter:
    """Extracts text previews from PDF documents, falling back to OCR when needed."""

    _SUPPORTED_MIME = "application/pdf"

    def supports(self, path: str, mime: str) -> bool:
        return (mime or "").lower() == self._SUPPORTED_MIME

    def read_preview(self, path: str, limit: int) -> str | None:
        if limit is not None and limit < 0:
            return None
        reader = PdfReader(path)
        if getattr(reader, "is_encrypted", False):
            if not reader.decrypt(""):
                return None
        collected: list[str] = []
        remaining = limit if limit else None
        total_chars = 0
        for page in self._iter_pages(reader):
            text = page.extract_text() or ""
            trimmed = text.strip()
            if not trimmed:
                continue
            total_chars += len(trimmed)
            if remaining is None:
                collected.append(text)
            else:
                if remaining <= 0:
                    break
                snippet = text[:remaining]
                collected.append(snippet)
                remaining -= len(snippet)
            if remaining is not None and remaining <= 0:
                break

        if collected and total_chars >= _MIN_TEXT_THRESHOLD:
            preview = "".join(collected)
            return preview[:limit] if limit else preview

        ocr_preview = self._ocr_images_preview(reader, limit)
        if ocr_preview:
            return ocr_preview

        if collected:
            preview = "".join(collected)
            return preview[:limit] if limit else preview
        return None

    def _ocr_images_preview(self, reader: PdfReader, limit: int) -> str | None:
        text_parts: list[str] = []
        for page in self._iter_pages(reader):
            images = getattr(page, "images", []) or []
            for image in images:
                try:
                    data = getattr(image, "data", None)
                    if data is None and hasattr(image, "image") and hasattr(image.image, "get_data"):
                        data = image.image.get_data()
                    if data is None:
                        continue
                    img = Image.open(BytesIO(data))
                    prepared = _prepare_image_for_ocr(img)
                    text = pytesseract.image_to_string(prepared).strip()
                    if text:
                        text_parts.append(text)
                except Exception:
                    continue
        if not text_parts:
            return None
        full_text = " ".join(text_parts)
        return full_text[:limit] if limit else full_text

    def _iter_pages(self, reader: PdfReader) -> Iterable:
        for page in reader.pages:
            yield page


def build() -> PeekImporter:
    return PdfImporter()


__all__ = ["PdfImporter", "build"]
