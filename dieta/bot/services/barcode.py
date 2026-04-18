from __future__ import annotations
"""
Decode a barcode from image bytes.
pyzbar/libzbar imported lazily so the bot starts even if the system
library is missing (decode_barcode will return None in that case).
"""
import io


def decode_barcode(image_bytes: bytes) -> str | None:
    """
    Return the first barcode value from image bytes, or None.
    Tries several preprocessing strategies to maximise read rate.
    """
    try:
        from PIL import Image, ImageEnhance, ImageFilter
        from pyzbar.pyzbar import decode
    except (ImportError, OSError):
        return None

    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")

    candidates = _make_candidates(img, ImageEnhance, ImageFilter)
    for candidate in candidates:
        codes = decode(candidate)
        if codes:
            return codes[0].data.decode("utf-8")
    return None


def _make_candidates(img, ImageEnhance, ImageFilter):
    """Generate progressively preprocessed versions of the image."""
    candidates = []

    # 1. Original as grayscale
    gray = img.convert("L")
    candidates.append(gray)

    # 2. Grayscale + contrast boost
    boosted = ImageEnhance.Contrast(gray).enhance(2.0)
    candidates.append(boosted)

    # 3. Sharpen
    sharpened = gray.filter(ImageFilter.SHARPEN)
    candidates.append(sharpened)

    # 4. Resize to common barcode-friendly widths if image is very large or small
    w, h = img.size
    for target_w in (800, 1200, 600):
        if abs(w - target_w) > 200:  # only if meaningfully different
            scale = target_w / w
            resized = gray.resize((target_w, int(h * scale)))
            candidates.append(resized)
            candidates.append(ImageEnhance.Contrast(resized).enhance(2.0))

    return candidates


def is_available() -> bool:
    """Return True if pyzbar + libzbar are loadable."""
    try:
        from pyzbar.pyzbar import decode  # noqa: F401
        return True
    except (ImportError, OSError):
        return False
