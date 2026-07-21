"""Logo normalization and brand-palette extraction for tenant PDF branding.

Pure Pillow + stdlib (no I/O, no network) so everything is unit-testable.
Used by the branding API endpoints: uploads are normalized to a bounded PNG
before being stored as a data-URL inside ``tenants.pdf_branding``.
"""
from __future__ import annotations

import io
import logging

from PIL import Image, UnidentifiedImageError

logger = logging.getLogger(__name__)

ALLOWED_FORMATS = {"PNG", "JPEG", "WEBP", "GIF", "BMP"}
MAX_INPUT_BYTES = 2 * 1024 * 1024
MAX_PIXELS = 30_000_000
MAX_DIM = 512


class LogoValidationError(ValueError):
    """Rejected logo upload; ``code`` is a stable machine-readable reason."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def normalize_logo(
    raw: bytes,
    *,
    max_dim: int = MAX_DIM,
    max_input_bytes: int = MAX_INPUT_BYTES,
    max_pixels: int = MAX_PIXELS,
) -> tuple[bytes, int, int]:
    """Validate *raw* image bytes and re-encode as PNG capped at *max_dim*.

    Returns ``(png_bytes, width, height)``. Raises LogoValidationError with
    code ``too_large`` / ``bad_format`` / ``too_many_pixels`` / ``corrupt``.
    Alpha is preserved; animated images keep only their first frame.
    """
    if len(raw) > max_input_bytes:
        raise LogoValidationError(
            "too_large", f"La imagen supera el máximo de {max_input_bytes} bytes"
        )
    if b"<svg" in raw[:1024].lower():
        raise LogoValidationError("bad_format", "SVG no soportado; usar PNG/JPG/WebP")

    try:
        img = Image.open(io.BytesIO(raw))
    except Image.DecompressionBombError as exc:
        raise LogoValidationError("too_many_pixels", "Imagen demasiado grande") from exc
    except (UnidentifiedImageError, OSError) as exc:
        raise LogoValidationError("corrupt", "No se pudo leer la imagen") from exc

    if img.format not in ALLOWED_FORMATS:
        raise LogoValidationError(
            "bad_format", f"Formato {img.format} no soportado; usar PNG/JPG/WebP"
        )
    if img.width * img.height > max_pixels:
        raise LogoValidationError(
            "too_many_pixels", f"La imagen supera el máximo de {max_pixels} píxeles"
        )

    try:
        img.load()  # full decode: lazy open() doesn't catch truncated data
    except OSError as exc:
        raise LogoValidationError("corrupt", "Imagen truncada o dañada") from exc

    if img.mode not in ("RGB", "RGBA"):
        has_alpha = "A" in img.mode or (
            img.mode == "P" and "transparency" in img.info
        )
        img = img.convert("RGBA" if has_alpha else "RGB")

    if max(img.size) > max_dim:
        img.thumbnail((max_dim, max_dim), Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue(), img.width, img.height


# ── WCAG 2.x contrast ────────────────────────────────────────────────────────

def _rel_luminance(rgb: tuple[int, int, int]) -> float:
    def lin(c: int) -> float:
        s = c / 255
        return s / 12.92 if s <= 0.04045 else ((s + 0.055) / 1.055) ** 2.4

    r, g, b = (lin(c) for c in rgb[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    """WCAG 2.x contrast ratio between two RGB colors (1.0 – 21.0)."""
    la, lb = _rel_luminance(a), _rel_luminance(b)
    lighter, darker = max(la, lb), min(la, lb)
    return (lighter + 0.05) / (darker + 0.05)


# ── Palette construction ─────────────────────────────────────────────────────

_WHITE = (255, 255, 255)
_DARK_TEXT = (51, 51, 51)
NEUTRAL_BASE = (51, 51, 51)
_NEAR_WHITE = 240
_DEDUPE_DIST = 40


def _hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def _mix(a: tuple, b: tuple, t: float) -> tuple[int, int, int]:
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _darken_until(rgb: tuple, against: tuple, target: float) -> tuple[int, int, int]:
    color = tuple(rgb[:3])
    for _ in range(40):
        if contrast_ratio(color, against) >= target:
            return color
        color = tuple(max(0, round(c * 0.92)) for c in color)
    return color


def build_palette(base: tuple[int, int, int]) -> dict[str, str]:
    """Derive an invoice palette (primary/accent/light_bg) from one base color.

    Guarantees: primary ≥4.5 contrast vs white paper (header/total boxes carry
    white text), accent ≥3.0 vs white (rules, secondary titles), light_bg
    readable under dark body text (≥4.5 vs #333).
    """
    primary = _darken_until(base, _WHITE, 4.5)
    accent = _darken_until(_mix(primary, _WHITE, 0.30), _WHITE, 3.0)
    light_bg = _mix(primary, _WHITE, 0.92)
    for _ in range(10):
        if contrast_ratio(light_bg, _DARK_TEXT) >= 4.5:
            break
        light_bg = _mix(light_bg, _WHITE, 0.5)
    return {"primary": _hex(primary), "accent": _hex(accent), "light_bg": _hex(light_bg)}


def _dominant_colors(img: Image.Image) -> list[tuple[int, int, int]]:
    """Most frequent colors, near-whites dropped, similar tones deduped."""
    quantized = img.quantize(colors=8, method=Image.Quantize.MEDIANCUT)
    palette = quantized.getpalette()
    counts = sorted(quantized.getcolors(maxcolors=256) or [], reverse=True)
    dominants: list[tuple[int, int, int]] = []
    for _count, idx in counts:
        rgb = tuple(palette[idx * 3 : idx * 3 + 3])
        if all(c >= _NEAR_WHITE for c in rgb):
            continue
        if any(
            sum((rgb[i] - seen[i]) ** 2 for i in range(3)) ** 0.5 < _DEDUPE_DIST
            for seen in dominants
        ):
            continue
        dominants.append(rgb)
    return dominants


def extract_palettes(png_bytes: bytes, max_candidates: int = 4) -> list[dict[str, str]]:
    """Candidate palettes from a normalized logo, most dominant color first.

    Always returns at least one palette (a neutral fallback), even for
    degenerate inputs (all-white, fully transparent, unreadable bytes).
    """
    dominants: list[tuple[int, int, int]] = []
    try:
        img = Image.open(io.BytesIO(png_bytes))
        img.load()
        if "A" in img.mode or (img.mode == "P" and "transparency" in img.info):
            rgba = img.convert("RGBA")
            white = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
            img = Image.alpha_composite(white, rgba).convert("RGB")
        else:
            img = img.convert("RGB")
        img.thumbnail((128, 128))
        dominants = _dominant_colors(img)
    except Exception:  # defensive: palette extraction must never fail hard
        logger.warning("[logo_palette] no se pudo analizar el logo", exc_info=True)

    result: list[dict[str, str]] = []
    seen_primaries: set[str] = set()
    for rgb in dominants[: max_candidates - 1]:
        palette = build_palette(rgb)
        if palette["primary"] not in seen_primaries:
            seen_primaries.add(palette["primary"])
            result.append(palette)
    neutral = build_palette(NEUTRAL_BASE)
    if neutral["primary"] not in seen_primaries:
        result.append(neutral)
    return result[:max_candidates]
