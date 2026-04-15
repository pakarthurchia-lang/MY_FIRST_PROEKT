from __future__ import annotations
"""
Decode a barcode from image bytes.
pyzbar/libzbar imported lazily so the bot starts even if the system
library is missing (decode_barcode will return None in that case).
"""
import io


def decode_barcode(image_bytes: bytes) -> str | None:
    """Return the first barcode value from image bytes, or None."""
    try:
        from PIL import Image
        from pyzbar.pyzbar import decode
    except (ImportError, OSError):
        return None

    img = Image.open(io.BytesIO(image_bytes))
    codes = decode(img)
    return codes[0].data.decode("utf-8") if codes else None
