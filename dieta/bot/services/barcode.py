from __future__ import annotations
"""
Decode a barcode from image bytes using pyzbar + Pillow.
Returns the first barcode string found, or None.
"""
import io

from PIL import Image
from pyzbar.pyzbar import decode


def decode_barcode(image_bytes: bytes) -> str | None:
    """Return the first barcode value from image bytes, or None."""
    img = Image.open(io.BytesIO(image_bytes))
    codes = decode(img)
    return codes[0].data.decode("utf-8") if codes else None
