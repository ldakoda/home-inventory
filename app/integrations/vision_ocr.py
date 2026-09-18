import logging
import re

from google.cloud import vision

logger = logging.getLogger(__name__)

_BARCODE_OR_CATALOG_NUMBER = re.compile(r"^[\d\s\-]{6,}$")


def extract_text_from_image(image_bytes: bytes) -> str:
    """Pull whatever text is printed on a photographed album cover, so it can
    be fed into the existing MusicBrainz search as a normal query -- this is
    OCR, not visual cover-art recognition, so it only works when the cover
    actually has legible text on it (title, artist, etc). Uses Cloud Vision's
    DOCUMENT_TEXT_DETECTION, which is tuned for dense/stylized text blocks
    (closer to what a magazine or album cover layout looks like) rather than
    TEXT_DETECTION, which is tuned for single lines of signage-style text.
    """
    client = vision.ImageAnnotatorClient()
    image = vision.Image(content=image_bytes)
    response = client.document_text_detection(image=image)
    if response.error.message:
        logger.error("Cloud Vision OCR failed: %s", response.error.message)
        return ""
    return (response.full_text_annotation.text or "").strip()


def text_to_search_query(text: str) -> str:
    """A front cover's OCR text is usually just artist + title (maybe a
    subtitle), in roughly reading order -- but it can also pick up a barcode,
    catalog number, or copyright line, and a back-cover photo would return a
    full tracklist. Keep only the first few real lines and drop anything that
    looks like a barcode/catalog number, rather than dumping the entire raw
    OCR blob into the search (which dilutes MusicBrainz's relevance ranking
    with noise words instead of giving it a clean "Artist Title" query).
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    lines = [line for line in lines if not _BARCODE_OR_CATALOG_NUMBER.match(line) and "©" not in line and "℗" not in line]
    return " ".join(lines[:3])
