"""Logo normalization + palette extraction with WCAG guarantees."""
from io import BytesIO

import pytest
from PIL import Image

from factura_pdf import (
    PRESETS,
    LogoValidationError,
    build_palette,
    contrast_ratio,
    extract_palettes,
    normalize_logo,
)

_WHITE = (255, 255, 255)
_DARK = (51, 51, 51)


def _png(color=(36, 86, 230), size=(64, 40)) -> bytes:
    buf = BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def test_normalize_logo_reencodes_to_bounded_png():
    png, w, h = normalize_logo(_png(size=(1200, 800)))
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert max(w, h) <= 512


def test_normalize_logo_rejects_svg():
    with pytest.raises(LogoValidationError) as exc:
        normalize_logo(b"<svg xmlns='http://www.w3.org/2000/svg'></svg>")
    assert exc.value.code == "bad_format"


def test_extract_palettes_always_returns_at_least_one():
    palettes = extract_palettes(_png())
    assert len(palettes) >= 1
    for p in palettes:
        assert set(p) == {"primary", "accent", "light_bg"}


def test_build_palette_meets_contrast():
    p = build_palette((36, 86, 230))
    primary = tuple(int(p["primary"][i : i + 2], 16) for i in (1, 3, 5))
    assert contrast_ratio(primary, _WHITE) >= 4.5


@pytest.mark.parametrize("name,spec", list(PRESETS.items()))
def test_presets_meet_wcag_contrast(name, spec):
    primary = spec["colors"]["primary"]
    light_bg = spec["colors"]["light_bg"]
    assert contrast_ratio(primary, _WHITE) >= 4.5, f"{name} primary vs white"
    assert contrast_ratio(light_bg, _DARK) >= 4.5, f"{name} light_bg vs #333"
