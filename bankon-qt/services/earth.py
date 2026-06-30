"""Blue Marble earth texture for the hyperreal globe — loaded once as a numpy array.

Real NASA Blue Marble imagery (public domain), equirectangular (lon −180..180, lat 90..−90).
Prefers the no-clouds image (crisp continents), downsampled to ≤2048 wide for fast sampling.
Returns None if numpy/Pillow/asset are unavailable → the globe falls back to vector coastlines.
"""
from pathlib import Path

_ASSETS = Path(__file__).resolve().parent.parent / "assets"
_tex = None
_tried = False


def earth_texture():
    global _tex, _tried
    if _tried:
        return _tex
    _tried = True
    try:
        import numpy as np
        from PIL import Image
        for name in ("earth_bm.jpg", "earth.jpg"):     # no-clouds first, then cloudy
            p = _ASSETS / name
            if p.exists():
                im = Image.open(p).convert("RGB")
                if im.width > 2048:
                    im = im.resize((2048, 1024), Image.BILINEAR)
                _tex = np.ascontiguousarray(np.asarray(im, dtype=np.uint8))   # (H, W, 3)
                break
    except Exception:
        _tex = None
    return _tex
