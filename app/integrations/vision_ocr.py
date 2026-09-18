import logging
import re

from google.cloud import vision

logger = logging.getLogger(__name__)

_BARCODE_OR_CATALOG_NUMBER = re.compile(r"^[\d\s\-]{6,}$")


def analyze_cover_photo(image_bytes: bytes) -> tuple[str, str]:
    """Read a photographed album cover two ways in one Vision API request:
    OCR (whatever text is actually printed on it) and web detection (Google's
    own best guess at what the image depicts, from reverse-image matching
    against the public web). Returns (ocr_text, web_guess).

    These are genuinely different signals -- OCR only works when the cover
    has legible printed text and can miss words on a stylized or partially
    framed shot; web detection matches the photo's actual pixels regardless
    of whether there's readable text at all, but depends on that specific
    cover already being indexed somewhere with decent metadata. The caller
    tries OCR first and falls back to the web guess only if that search
    comes up empty, since the web guess is a loose natural-language phrase
    (e.g. "rumors of fleetwood mac") rather than a clean search query.
    """
    client = vision.ImageAnnotatorClient()
    image = vision.Image(content=image_bytes)
    request = vision.AnnotateImageRequest(
        image=image,
        features=[
            vision.Feature(type_=vision.Feature.Type.DOCUMENT_TEXT_DETECTION),
            vision.Feature(type_=vision.Feature.Type.WEB_DETECTION),
        ],
    )
    response = client.annotate_image(request=request)
    if response.error.message:
        logger.error("Cloud Vision request failed: %s", response.error.message)
        return "", ""

    text = (response.full_text_annotation.text or "").strip()
    labels = response.web_detection.best_guess_labels
    web_guess = labels[0].label if labels else ""
    return text, web_guess


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
