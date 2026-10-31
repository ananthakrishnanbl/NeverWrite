"""Default sample glyph generation and seeding.

Provides a full default glyph library for any newly created profile so that:
1. Preview and Composer work immediately without missing character errors.
2. The user has a reference sample for all characters.
3. When scans are uploaded, the system can match and identify the most
   identifiable handwritten glyphs and update them.
"""
from pathlib import Path
from typing import List, Tuple
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from sqlalchemy.orm import Session

from app.config import DATA_DIR
from app.models.glyph import Glyph
from app.storage import save_glyph_mask

ALL_CHARS = (
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789.,!?;:'\"-()"
)

DESCENDERS = set("gjpqy,;")

def _get_font(size: int = 38) -> ImageFont.ImageFont:
    """Load a clean, natural handwriting or sans font from Windows Fonts."""
    for font_name in ["comic.ttf", "calibri.ttf", "segoeui.ttf", "arial.ttf"]:
        p = Path(r"C:\Windows\Fonts") / font_name
        if p.exists():
            try:
                return ImageFont.truetype(str(p), size)
            except Exception:
                pass
    return ImageFont.load_default()

def generate_sample_crop(char: str, font: ImageFont.ImageFont) -> Tuple[np.ndarray, float]:
    """Generate a clean, tight, 4-channel transparent PNG mask for a character.

    Returns
    -------
    (bgra_crop, baseline_offset)
    """
    canvas_size = 90
    im = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(im)
    draw.text((20, 20), char, fill=(25, 25, 35, 255), font=font)

    bbox = im.getbbox()
    if bbox is None:
        # Fallback 10x10 empty box
        return np.zeros((10, 10, 4), dtype=np.uint8), 0.0

    crop = im.crop(bbox)
    bgra = np.array(crop)

    # Baseline offset
    bh = bgra.shape[0]
    if char in DESCENDERS:
        baseline_offset = float(bh * 0.35)
    else:
        baseline_offset = 0.0

    return bgra, baseline_offset

def seed_profile_default_glyphs(profile_id: int, db: Session) -> int:
    """Seed a Profile with default sample glyphs for all standard characters.

    Only inserts glyphs that do not already exist for this profile.
    """
    font = _get_font(size=38)
    count = 0

    for char in ALL_CHARS:
        existing = (
            db.query(Glyph)
            .filter(Glyph.profile_id == profile_id, Glyph.char == char)
            .first()
        )
        if existing:
            continue

        bgra, baseline_offset = generate_sample_crop(char, font)
        bh, bw = bgra.shape[:2]
        if bh == 0 or bw == 0:
            continue

        # Save transparent mask PNG
        success, png_buf = cv2.imencode(".png", bgra)
        if not success:
            continue

        rel_path = save_glyph_mask(
            data=png_buf.tobytes(),
            profile_id=profile_id,
            char=char,
            variant=0,
        )

        glyph = Glyph(
            profile_id=profile_id,
            char=char,
            variant_index=0,
            mask_path=rel_path,
            svg_path=None,
            width=float(bw),
            height=float(bh),
            baseline_offset=baseline_offset,
            bearing_left=3.0,
            bearing_right=3.0,
            accepted=True,
        )
        db.add(glyph)
        count += 1

    db.commit()
    return count
