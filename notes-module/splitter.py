import fitz  # pymupdf
from config import DPI


def pdf_to_images(pdf_path: str, dpi: int = DPI) -> list[bytes]:
    """Return one PNG bytes object per page of the PDF."""
    doc = fitz.open(pdf_path)
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    return [
        page.get_pixmap(matrix=mat, colorspace=fitz.csRGB).tobytes("png")
        for page in doc
    ]
