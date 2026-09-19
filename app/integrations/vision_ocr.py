import logging
import re

from google.cloud import vision

logger = logging.getLogger(__name__)

_BARCODE_OR_CATALOG_NUMBER = re.compile(r"^[\d\s\-]{6,}$")

# A cover's actual title/artist typography is the biggest text on it -- a
# promotional sticker ("Limited Edition", "Exclusive", a retailer's own
# tagline), a barcode, or a catalog number is printed much smaller. Requiring
# a text block's height to be a decent fraction of the whole photo's height
# throws out that small print without needing to guess at specific phrases
# (a real "Limited Edition Silver Vinyl / Only at Walmart" sticker measured
# well under this on a real photo that also had genuine 100-400px-tall cover
# typography in the same shot).
_MIN_PROMINENT_HEIGHT_FRACTION = 0.06

# When two (or more) records are photographed side by side, their prominent
# text blocks cluster into separate horizontal groups with a wide gap
# between them -- requiring that gap to be a decent fraction of the photo's
# width is what tells two different covers apart from one cover's own title
# sitting close above its subtitle.
_MIN_CLUSTER_GAP_FRACTION = 0.12


def _extract_prominent_blocks(image_bytes: bytes) -> tuple[list[dict], str]:
    """Cloud Vision's OCR returns each detected block of text with its own
    bounding box -- using that instead of just the flattened text string is
    what makes it possible to tell a cover's real title/artist typography
    apart from a promo sticker or barcode (see _MIN_PROMINENT_HEIGHT_FRACTION),
    and to notice when a photo actually has more than one record in it (see
    extract_cover_queries). Also runs web detection in the same request.
    Returns (blocks, web_guess); each block is
    {"text", "height", "x_min", "x_max", "y_min"}.
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
        return [], ""

    labels = response.web_detection.best_guess_labels
    web_guess = labels[0].label if labels else ""

    pages = response.full_text_annotation.pages
    if not pages:
        return [], web_guess

    photo_height = pages[0].height or 1
    min_height = photo_height * _MIN_PROMINENT_HEIGHT_FRACTION

    blocks = []
    for block in pages[0].blocks:
        words = [
            "".join(symbol.text for symbol in word.symbols)
            for paragraph in block.paragraphs
            for word in paragraph.words
        ]
        text = " ".join(w for w in words if w).strip()
        if not text or _BARCODE_OR_CATALOG_NUMBER.match(text):
            continue

        verts = block.bounding_box.vertices
        xs = [v.x for v in verts]
        ys = [v.y for v in verts]
        height = max(ys) - min(ys)
        if height < min_height:
            continue

        blocks.append({"text": text, "height": height, "x_min": min(xs), "x_max": max(xs), "y_min": min(ys)})

    return blocks, web_guess


def extract_cover_queries(image_bytes: bytes) -> tuple[list[str], str]:
    """Turn a photographed cover into one search query per record detected in
    it (almost always just one) plus the web-detection best-guess label for
    the whole photo. Filters small print (stickers, barcodes, retailer
    taglines) out by prominence, then splits what's left into separate
    covers by looking for a wide horizontal gap between blocks -- so one
    photo of two records side by side can identify both instead of mashing
    both sets of title/artist text into a single, unsearchable query.
    """
    blocks, web_guess = _extract_prominent_blocks(image_bytes)
    if not blocks:
        return [], web_guess

    blocks.sort(key=lambda b: b["x_min"])
    photo_width = max(b["x_max"] for b in blocks)
    min_gap = photo_width * _MIN_CLUSTER_GAP_FRACTION

    clusters: list[list[dict]] = [[blocks[0]]]
    cluster_max_x = blocks[0]["x_max"]
    for b in blocks[1:]:
        if b["x_min"] - cluster_max_x > min_gap:
            clusters.append([])
        clusters[-1].append(b)
        cluster_max_x = max(cluster_max_x, b["x_max"])

    queries = []
    for cluster in clusters:
        cluster.sort(key=lambda b: b["y_min"])
        query = " ".join(b["text"] for b in cluster[:4]).strip()
        if query:
            queries.append(query)

    return queries, web_guess
