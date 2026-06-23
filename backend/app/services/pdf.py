import io
import structlog
from typing import Optional

logger = structlog.get_logger()


def extract_text_from_pdf(pdf_bytes: bytes) -> Optional[str]:
    """
    Extract text from PDF bytes.
    Strategy:
      1. Try pdfplumber (fast, native text)
      2. If result is blank/too short → fall back to PyMuPDF
      3. If still blank → caller should trigger Gemini Vision OCR
    """
    text = _extract_with_pdfplumber(pdf_bytes)

    if text and len(text.strip()) > 100:
        logger.info("pdf_extracted", method="pdfplumber", chars=len(text))
        return text.strip()

    # Fallback to PyMuPDF
    text = _extract_with_pymupdf(pdf_bytes)
    if text and len(text.strip()) > 100:
        logger.info("pdf_extracted", method="pymupdf", chars=len(text))
        return text.strip()

    logger.warning("pdf_text_empty", note="likely scanned PDF, needs OCR")
    return None


def _extract_with_pdfplumber(pdf_bytes: bytes) -> Optional[str]:
    """Use pdfplumber for native PDF text extraction."""
    try:
        import pdfplumber

        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            pages_text = []
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    pages_text.append(page_text)
            return "\n".join(pages_text)
    except Exception as e:
        logger.warning("pdfplumber_failed", error=str(e))
        return None


def _extract_with_pymupdf(pdf_bytes: bytes) -> Optional[str]:
    """Use PyMuPDF (fitz) as secondary extraction method."""
    try:
        import fitz  # PyMuPDF

        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        pages_text = []
        for page in doc:
            pages_text.append(page.get_text())
        doc.close()
        return "\n".join(pages_text)
    except Exception as e:
        logger.warning("pymupdf_failed", error=str(e))
        return None


def clean_text(text: str) -> str:
    """Remove excessive whitespace and normalize text."""
    import re
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r'[ \t]+', ' ', text)
    return text.strip()


def is_valid_pdf(content: bytes) -> bool:
    """Check if bytes represent a valid PDF by checking the magic bytes."""
    return content[:4] == b'%PDF'
