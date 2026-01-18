from __future__ import annotations

from .interface import PeekImporter

from PIL import Image
import easyocr
import resource
import subprocess
import tempfile
import os

from ..config import config
from ..utils import log

# Disable core dumps to prevent large files from causing issues
try:
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
except ValueError:
    pass  # Not supported on all systems

_MIN_DIMENSION_FOR_RELIABLE_OCR = 600
_MAX_OCR_SCALE = 4.0
_MAX_OCR_DIMENSION = 4096


def _prepare_image_for_ocr(image: Image.Image) -> Image.Image:
    """Upscale and normalize images so Tesseract keeps leading characters."""
    if not isinstance(image, Image.Image):
        return image
    width, height = image.size
    min_dimension = min(width, height)
    if 0 < min_dimension < _MIN_DIMENSION_FOR_RELIABLE_OCR:
        target_scale = _MIN_DIMENSION_FOR_RELIABLE_OCR / min_dimension
        scale = min(_MAX_OCR_SCALE, target_scale)
        new_width = int(width * scale)
        new_height = int(height * scale)
        if new_width != width or new_height != height:
            image = image.resize((new_width, new_height), Image.Resampling.LANCZOS)
    return image.convert('L')


def _run_tesseract(path: str, timeout: int) -> str:
    """Run tesseract binary directly with hard timeout."""
    # Include original filename in temp file for debugging
    original_basename = os.path.basename(path)
    name_part = os.path.splitext(original_basename)[0][:50]  # Limit length
    
    with tempfile.NamedTemporaryFile(suffix=f'.{name_part}.png', delete=False) as tmp_img:
        tmp_img_path = tmp_img.name
    
    with tempfile.NamedTemporaryFile(suffix=f'.{name_part}', delete=False) as tmp_out:
        tmp_out_base = tmp_out.name
    
    try:
        # Prepare image
        with Image.open(path) as img:
            prepared = _prepare_image_for_ocr(img)
            prepared.save(tmp_img_path, 'PNG')
        
        # Run tesseract with hard timeout
        result = subprocess.run(
            ['tesseract', tmp_img_path, tmp_out_base],
            timeout=timeout,
            capture_output=True,
            text=True
        )
        
        # Read output
        output_file = f"{tmp_out_base}.txt"
        if os.path.exists(output_file):
            with open(output_file, 'r', encoding='utf-8') as f:
                return f.read().strip()
        return ""
        
    finally:
        # Cleanup temp files
        for fpath in [tmp_img_path, tmp_out_base, f"{tmp_out_base}.txt"]:
            try:
                if os.path.exists(fpath):
                    os.unlink(fpath)
            except:
                pass


class FallbackOCRImporter:
    """Fallback OCR using EasyOCR."""

    def __init__(self, timeout_seconds: int | None = None):
        self._reader = None  # Lazy load on first use
        timeout = timeout_seconds if timeout_seconds and timeout_seconds > 0 else config.OCR_TIMEOUT_SECONDS
        self._timeout = max(1, int(timeout))

    def _get_reader(self):
        """Lazy load EasyOCR reader only when needed."""
        if self._reader is None:
            log.debug("lazy_loading_easyocr")
            self._reader = easyocr.Reader(['en'])
        return self._reader

    def supports(self, path: str, mime: str) -> bool:
        return mime.startswith('image/')

    def read_preview(self, path: str, limit: int) -> str | None:
        filename = os.path.basename(path)
        log.info("fallback_ocr_start", path=path, filename=filename)
        try:
            reader = self._get_reader()
            results = reader.readtext(path, detail=0)
            text = ' '.join(results).strip()
            if text:
                return text[:limit]
            return None
        except Exception as e:
            log.warning("fallback_ocr_exception", path=path, filename=filename, error=str(e))
            return None


class OCRImporter:
    """Extracts text previews from images using OCR, detecting scanned documents."""

    def __init__(self, timeout_seconds: int | None = None):
        timeout = timeout_seconds if timeout_seconds and timeout_seconds > 0 else config.OCR_TIMEOUT_SECONDS
        self._timeout = max(1, int(timeout))
        self._fallback = FallbackOCRImporter(timeout_seconds)

    def supports(self, path: str, mime: str) -> bool:
        return mime.startswith('image/')

    def read_preview(self, path: str, limit: int) -> str | None:
        filename = os.path.basename(path)
        log.info("ocr_start", path=path, filename=filename, timeout=self._timeout)
        try:
            text = _run_tesseract(path, self._timeout)
            if not text:
                return None  # No text found, likely not a scanned document
            return text[:limit]
        except subprocess.TimeoutExpired:
            log.warning("ocr_timeout", path=path, filename=filename, timeout=self._timeout)
            # Try fallback
            return self._fallback.read_preview(path, limit)
        except (subprocess.CalledProcessError, OSError) as exc:
            log.warning("ocr_failed", path=path, filename=filename, error=str(exc))
            # Try fallback
            return self._fallback.read_preview(path, limit)
        except Exception as exc:
            log.warning("ocr_unexpected_error", path=path, filename=filename, error=str(exc))
            # Try fallback
            return self._fallback.read_preview(path, limit)


def build(timeout_seconds: int | None = None) -> PeekImporter:
    return OCRImporter(timeout_seconds)


__all__ = ["OCRImporter", "build"]
