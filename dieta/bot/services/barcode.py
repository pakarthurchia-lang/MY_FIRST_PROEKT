from __future__ import annotations
"""
Decode a barcode from image bytes using pyzbar + libzbar0.
libzbar is pre-loaded via ctypes with explicit paths so pyzbar
can find it even when the dynamic linker cache is stale.
"""
import ctypes
import io

_LIBZBAR_PATHS = [
    "libzbar.so.0",
    "/usr/lib/x86_64-linux-gnu/libzbar.so.0",
    "/usr/lib/aarch64-linux-gnu/libzbar.so.0",
    "/usr/lib/libzbar.so.0",
    "/usr/local/lib/libzbar.so.0",
]

def _preload_libzbar() -> bool:
    for path in _LIBZBAR_PATHS:
        try:
            ctypes.cdll.LoadLibrary(path)
            return True
        except OSError:
            continue
    return False

_libzbar_loaded = _preload_libzbar()


def decode_barcode(image_bytes: bytes) -> str | None:
    """Return the first barcode value from image bytes, or None."""
    try:
        from PIL import Image, ImageEnhance, ImageFilter
        from pyzbar.pyzbar import decode
    except (ImportError, OSError):
        return None

    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    for candidate in _make_candidates(img, ImageEnhance, ImageFilter):
        codes = decode(candidate)
        if codes:
            return codes[0].data.decode("utf-8")
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
        from pyzbar.pyzbar import decode  # noqa: F401
        return True
    except (ImportError, OSError):
        return False
