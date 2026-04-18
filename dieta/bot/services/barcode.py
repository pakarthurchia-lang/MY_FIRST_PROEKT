from __future__ import annotations
"""
Decode a barcode from image bytes using zxingcpp.
zxingcpp bundles the C++ library inside the Python wheel —
no system package required.
"""
import io


def decode_barcode(image_bytes: bytes) -> str | None:
    """Return the first barcode value from image bytes, or None."""
    try:
        import zxingcpp
        from PIL import Image, ImageEnhance, ImageFilter
    except ImportError:
        return None

    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")

    for candidate in _make_candidates(img, ImageEnhance, ImageFilter):
        results = zxingcpp.read_barcodes(candidate)
        if results:
            return results[0].text
    return None


def _make_candidates(img, ImageEnhance, ImageFilter):
    gray = img.convert("L")
    yield gray
    yield ImageEnhance.Contrast(gray).enhance(2.0)
    yield gray.filter(ImageFilter.SHARPEN)

    w, h = img.size
    for target_w in (800, 1200, 600):
        if abs(w - target_w) > 200:
            scale = target_w / w
            resized = gray.resize((target_w, int(h * scale)))
            yield resized
            yield ImageEnhance.Contrast(resized).enhance(2.0)


def is_available() -> bool:
    try:
        import zxingcpp  # noqa: F401
        return True
    except ImportError:
        return False
