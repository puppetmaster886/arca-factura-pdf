"""Render Argentine invoice / proforma PDFs (ARCA/AFIP layout) with branding.

``generate_invoice_pdf(..., fiscal=True)`` produces a complete AFIP comprobante
(QR + CAE) from a real authorization; ``fiscal=False`` (and
``doc_kind="proforma"``) produces a clearly-watermarked non-fiscal document.
Pure ``dict`` in → PDF ``bytes`` out; no network, no database, no secrets.
"""
import base64
import calendar
import datetime
import json
import logging
import os
from dataclasses import dataclass, field, fields
from io import BytesIO
from pathlib import Path

import qrcode
from fpdf import FPDF
from PIL import Image

logger = logging.getLogger(__name__)

# ── Comprobante metadata ─────────────────────────────────────────────────────
TIPO_CBTE_LETRA = {1: "A", 6: "B", 11: "C"}
TIPO_CBTE_COD   = {1: "COD. 01", 6: "COD. 06", 11: "COD. 11"}
ALICUOTA_LABEL  = {3: "0%", 4: "10,5%", 5: "21%", 6: "27%", 8: "5%", 9: "2,5%"}

# ── Brand colors (R, G, B) ───────────────────────────────────────────────────
C_NAVY  = (46,  49,  146)
C_CYAN  = (41,  171, 226)
C_TEAL  = (0,   169, 157)
C_LBLUE = (244, 246, 255)
C_WHITE = (255, 255, 255)
C_DARK  = (51,  51,  51)
C_MID   = (100, 100, 100)


# ── Branding dataclass — replaces module-level env globals ──────────────────
# Maps dataclass field names → env var names for branding_from_env().
_FIELD_TO_ENV = {
    "brand_name":      "PDF_BRAND_NAME",
    "brand_tagline":   "PDF_BRAND_TAGLINE",
    "emisor_nombre":   "PDF_EMISOR_NOMBRE",
    "emisor_razon_social": "PDF_EMISOR_RAZON_SOCIAL",
    "emisor_domicilio": "PDF_EMISOR_DOMICILIO",
    "emisor_iibb":     "PDF_EMISOR_IIBB",
    "emisor_inicio_act": "PDF_EMISOR_INICIO_ACTIVIDADES",
    "emisor_iva":      "PDF_EMISOR_IVA",
    "contact_tel":     "PDF_CONTACT_TEL",
    "contact_email":   "PDF_CONTACT_EMAIL",
    "contact_web":     "PDF_CONTACT_WEB",
    "premium_msg":     "PDF_PREMIUM_MSG",
    "logo_path":       "PDF_LOGO_PATH",
}


# ── Style system (branding v2) ───────────────────────────────────────────────
# Every preset primary must keep ≥4.5 WCAG contrast against white (header and
# total boxes carry white text) and light_bg ≥4.5 under #333 body text —
# enforced by test_presets_meet_wcag_contrast.
PRESETS = {
    "clasico": {
        "colors": {"primary": C_NAVY, "accent": C_CYAN, "light_bg": C_LBLUE},
        "font": "helvetica",
        "table": "zebra",
        "density": "normal",
    },
    "moderno": {
        "colors": {"primary": (0, 121, 107), "accent": (0, 150, 136), "light_bg": (230, 244, 242)},
        "font": "inter",
        "table": "minimal",
        "density": "airy",
    },
    "minimal": {
        "colors": {"primary": (51, 51, 51), "accent": (102, 102, 102), "light_bg": (245, 245, 245)},
        "font": "inter",
        "table": "rules",
        "density": "compact",
    },
    "corporativo": {
        "colors": {"primary": (31, 58, 95), "accent": (74, 111, 165), "light_bg": (238, 242, 247)},
        "font": "inter",
        "table": "rules",
        "density": "normal",
    },
    "elegante": {
        "colors": {"primary": (91, 35, 51), "accent": (138, 74, 92), "light_bg": (249, 241, 243)},
        "font": "source_serif",
        "table": "zebra",
        "density": "airy",
    },
}

FONT_KEYS = ("helvetica", "inter", "source_serif", "dejavu")
TABLE_STYLES = ("zebra", "rules", "minimal")
DENSITIES = ("compact", "normal", "airy")
LOGO_POSITIONS = ("left", "center", "right")
LOGO_SIZES = ("s", "m", "l")
IVA_BREAKDOWNS = ("all", "main", "total_only")
IVA_COLUMNS = ("auto", "show", "hide")
LAYOUTS = ("expandido", "compacto")


@dataclass
class PdfStyle:
    """Resolved visual style (preset already overlaid)."""

    preset: str = "clasico"
    primary: tuple = C_NAVY
    accent: tuple = C_CYAN
    light_bg: tuple = C_LBLUE
    font: str = "helvetica"
    table: str = "zebra"
    density: str = "normal"


@dataclass
class PdfLogo:
    data_url: str = ""
    position: str = "left"
    size: str = "m"


@dataclass
class PdfOptions:
    iva_breakdown: str = "all"
    iva_column: str = "auto"
    prices_include_iva: bool = False
    legal_legends: bool = True
    show_customer_notes: bool = False
    show_proforma: bool = True
    show_periodo: bool = True
    layout: str = "expandido"  # bloque de totales/CAE anclado al pie de página
    col_code: bool = True
    col_discount: bool = True
    col_unit: bool = True
    pay_titular: str = ""
    pay_cbu: str = ""
    pay_alias: str = ""
    pay_banco: str = ""
    pay_medios: str = ""
    terms: str = ""


@dataclass
class PdfBranding:
    """Per-tenant branding for ARCA invoice PDFs."""

    brand_name: str = ""
    brand_tagline: str = ""
    emisor_nombre: str = ""
    emisor_razon_social: str = ""
    emisor_domicilio: str = ""
    emisor_iibb: str = ""
    emisor_inicio_act: str = ""
    emisor_iva: str = "IVA Responsable Inscripto"
    contact_tel: str = ""
    contact_email: str = ""
    contact_web: str = ""
    premium_msg: str = ""
    logo_path: str = ""
    style: PdfStyle = field(default_factory=PdfStyle)
    logo: PdfLogo = field(default_factory=PdfLogo)
    options: PdfOptions = field(default_factory=PdfOptions)


def branding_from_env() -> PdfBranding:
    """Build a PdfBranding from PDF_* environment variables (CLI / single-tenant)."""
    kwargs = {}
    for attr, env_key in _FIELD_TO_ENV.items():
        val = os.getenv(env_key, "")
        if val:
            kwargs[attr] = val
    return PdfBranding(**kwargs)


def _hex_to_rgb(value) -> tuple | None:
    """'#rrggbb' → (r, g, b); None on anything malformed."""
    if not isinstance(value, str) or len(value) != 7 or not value.startswith("#"):
        return None
    try:
        return tuple(int(value[i : i + 2], 16) for i in (1, 3, 5))
    except ValueError:
        return None


def _enum_or(value, allowed: tuple, fallback: str) -> str:
    return value if value in allowed else fallback


def _parse_style(raw, base: PdfStyle) -> PdfStyle:
    """Tolerant parse of the ``style`` sub-dict; bad values fall back to *base*."""
    raw = raw if isinstance(raw, dict) else {}
    preset = raw.get("preset", base.preset)
    if preset != "custom" and preset not in PRESETS:
        preset = base.preset if base.preset in PRESETS or base.preset == "custom" else "clasico"

    if preset in PRESETS:
        spec = PRESETS[preset]
        return PdfStyle(preset=preset, **spec["colors"],
                        font=spec["font"], table=spec["table"], density=spec["density"])

    colors = raw.get("colors") if isinstance(raw.get("colors"), dict) else {}
    return PdfStyle(
        preset="custom",
        primary=_hex_to_rgb(colors.get("primary")) or base.primary,
        accent=_hex_to_rgb(colors.get("accent")) or base.accent,
        light_bg=_hex_to_rgb(colors.get("light_bg")) or base.light_bg,
        font=_enum_or(raw.get("font", base.font), FONT_KEYS, base.font),
        table=_enum_or(raw.get("table", base.table), TABLE_STYLES, base.table),
        density=_enum_or(raw.get("density", base.density), DENSITIES, base.density),
    )


def _parse_logo(raw, base: PdfLogo) -> PdfLogo:
    raw = raw if isinstance(raw, dict) else {}
    data_url = raw.get("data_url", base.data_url)
    return PdfLogo(
        data_url=data_url if isinstance(data_url, str) else base.data_url,
        position=_enum_or(raw.get("position", base.position), LOGO_POSITIONS, base.position),
        size=_enum_or(raw.get("size", base.size), LOGO_SIZES, base.size),
    )


def _parse_options(raw, base: PdfOptions) -> PdfOptions:
    raw = raw if isinstance(raw, dict) else {}

    def _bool(key, fallback):
        val = raw.get(key, fallback)
        return val if isinstance(val, bool) else fallback

    columns = raw.get("columns") if isinstance(raw.get("columns"), dict) else {}

    def _col(key, fallback):
        val = columns.get(key, fallback)
        return val if isinstance(val, bool) else fallback

    payment = raw.get("payment") if isinstance(raw.get("payment"), dict) else {}

    def _pay(key, fallback):
        val = payment.get(key, fallback)
        return val if isinstance(val, str) else fallback

    terms = raw.get("terms", base.terms)
    return PdfOptions(
        iva_breakdown=_enum_or(raw.get("iva_breakdown", base.iva_breakdown), IVA_BREAKDOWNS, base.iva_breakdown),
        iva_column=_enum_or(raw.get("iva_column", base.iva_column), IVA_COLUMNS, base.iva_column),
        prices_include_iva=_bool("prices_include_iva", base.prices_include_iva),
        legal_legends=_bool("legal_legends", base.legal_legends),
        show_customer_notes=_bool("show_customer_notes", base.show_customer_notes),
        show_proforma=_bool("show_proforma", base.show_proforma),
        show_periodo=_bool("show_periodo", base.show_periodo),
        layout=_enum_or(raw.get("layout", base.layout), LAYOUTS, base.layout),
        col_code=_col("code", base.col_code),
        col_discount=_col("discount", base.col_discount),
        col_unit=_col("unit", base.col_unit),
        pay_titular=_pay("titular", base.pay_titular),
        pay_cbu=_pay("cbu", base.pay_cbu),
        pay_alias=_pay("alias", base.pay_alias),
        pay_banco=_pay("banco", base.pay_banco),
        pay_medios=_pay("medios", base.pay_medios),
        terms=terms if isinstance(terms, str) else base.terms,
    )


def branding_from_dict(overrides: dict, base: PdfBranding | None = None) -> PdfBranding:
    """Layer a dict of overrides (e.g. from tenants.pdf_branding JSON) over a base.

    Text fields present and non-empty in *overrides* win; everything else falls
    back to *base* (which defaults to branding_from_env() if not given). The
    nested ``style`` / ``logo`` / ``options`` objects (branding v2) are parsed
    tolerantly: unknown keys are ignored and invalid values fall back — a
    corrupt DB row must degrade, never crash a sync.
    """
    base = base or branding_from_env()
    kwargs = {}
    for f in fields(base):
        if not isinstance(getattr(base, f.name), str):
            continue
        override_val = overrides.get(f.name, "")
        kwargs[f.name] = (
            override_val
            if override_val and isinstance(override_val, str)
            else getattr(base, f.name)
        )
    kwargs["style"] = _parse_style(overrides.get("style"), base.style)
    kwargs["logo"] = _parse_logo(overrides.get("logo"), base.logo)
    kwargs["options"] = _parse_options(overrides.get("options"), base.options)
    return PdfBranding(**kwargs)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _fmt_cuit(cuit: str) -> str:
    d = "".join(c for c in str(cuit or "") if c.isdigit())
    if len(d) == 11:
        return f"{d[:2]}-{d[2:10]}-{d[10]}"
    return str(cuit or "")


def _fmt_date(s: str) -> str:
    s = str(s or "").replace("-", "")
    if len(s) == 8:
        return f"{s[6:8]}/{s[4:6]}/{s[0:4]}"
    return s


def _fmt_money(amount: float) -> str:
    parts = f"{amount:,.2f}".split(".")
    integer = parts[0].replace(",", ".")
    return f"${integer},{parts[1]}"


def _fmt_cbte_number(punto_venta: int, nro: int) -> str:
    return f"{punto_venta:04d}-{nro:08d}"


def _cuit_to_doc(cuit_str: str) -> tuple[int, int]:
    digits = "".join(c for c in str(cuit_str or "") if c.isdigit())
    if len(digits) == 11:
        return 80, int(digits)
    return 99, 0


def _receptor_iva(doc_nro: int) -> str:
    return "Consumidor Final" if doc_nro == 0 else "IVA Responsable Inscripto"


def _periodo_dates(fecha: str, concepto: int) -> tuple[str | None, str | None]:
    """Return (desde_fmt, hasta_fmt) for service period; (None, None) for products."""
    if concepto not in (2, 3):
        return None, None
    d = datetime.date.fromisoformat(fecha)
    last_day = calendar.monthrange(d.year, d.month)[1]
    desde = d.replace(day=1).strftime("%Y%m%d")
    hasta = d.replace(day=last_day).strftime("%Y%m%d")
    return _fmt_date(desde), _fmt_date(hasta)


def _alicuota_label(alicuota_id: int) -> str:
    label = ALICUOTA_LABEL.get(alicuota_id, str(alicuota_id))
    return f"IVA {label}:"


def _build_qr_url(arca_data: dict, result: dict, cuit_emisor: str) -> str:
    doc_tipo, doc_nro = _cuit_to_doc(arca_data.get("cuit_receptor", ""))
    qr_payload = {
        "ver":        1,
        "fecha":      arca_data["fecha"],
        "cuit":       int("".join(c for c in cuit_emisor if c.isdigit())),
        "ptoVta":     arca_data["punto_venta"],
        "tipoCmp":    arca_data["tipo_cbte"],
        "nroCmp":     result["nro_comprobante"],
        "importe":    float(arca_data["importe_total"]),
        "moneda":     "PES",
        "ctz":        1,
        "tipoDocRec": doc_tipo,
        "nroDocRec":  doc_nro,
        "tipoCodAut": "E",
        "codAut":     int(result["cae"]),
    }
    json_bytes = json.dumps(qr_payload, separators=(",", ":")).encode()
    b64 = base64.b64encode(json_bytes).decode()
    return f"https://www.afip.gob.ar/fe/qr/?p={b64}"


def _build_qr_png(url: str) -> bytes:
    qr = qrcode.QRCode(box_size=3, border=1)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ── Layout constants (mm) ────────────────────────────────────────────────────
LM = 10          # left margin
CW = 190         # content width (A4 210mm - 20mm margins)

# Header column layout
LEFT_W  = 88     # emisor column width
CTR_X   = LM + LEFT_W + 1   # = 99
CTR_W   = 18     # center (letter box) column width
RIGHT_X = CTR_X + CTR_W + 1  # = 118
RIGHT_W = CW - LEFT_W - CTR_W - 2  # = 82

# Items table column widths live in _item_cols() (sum = 190mm before drops).


# ── Style-driven rendering helpers ───────────────────────────────────────────

ROW_CUTOFF = 270    # y beyond which item rows continue on a new page
PAGE_BOTTOM = 285   # y limit for any block before forcing a page break

# density → (item row height mm, item font size pt)
DENSITY_SPECS = {"compact": (5.0, 7.0), "normal": (6.0, 7.5), "airy": (7.5, 8.0)}

# logo size key → rendered height in mm ("m" matches the historical 12 mm)
LOGO_HEIGHTS = {"s": 9, "m": 12, "l": 18}

HEADER_Y = 18            # y donde arranca el header de 3 columnas
_LOGO_GAP = 1            # margen sobre Y bajo el logo (iguales por diseño)
_LOGO_MAX_W = 44         # el logo nunca invade la columna central


def _logo_layout(logo: PdfLogo, aspect: float | None) -> dict:
    """Pure header geometry: the logo never overlaps text.

    Returns logo box + text anchors. ``aspect=None`` means "no logo" and
    yields the historical geometry (brand text at LM+14, fields at
    HEADER_Y+14). The gap below the logo always equals the gap above it.
    """
    base = {
        "logo_x": LM, "logo_y": HEADER_Y + _LOGO_GAP,
        "logo_w": 0.0, "logo_h": 0.0,
        "text_x": LM + 14, "brand_y": HEADER_Y + 1, "fields_y": HEADER_Y + 14,
    }
    if aspect is None:
        return base

    h = float(LOGO_HEIGHTS.get(logo.size, 12))
    w = h * aspect
    if w > _LOGO_MAX_W:
        w, h = float(_LOGO_MAX_W), _LOGO_MAX_W / aspect
    x = {
        "left": LM,
        "center": LM + (LEFT_W - w) / 2,
        "right": LM + LEFT_W - w,
    }.get(logo.position, LM)

    out = {**base, "logo_x": x, "logo_w": w, "logo_h": h}
    logo_bottom = HEADER_Y + _LOGO_GAP + h + _LOGO_GAP
    if logo.position == "left":
        out["text_x"] = LM + max(14, w + 2)
        out["fields_y"] = max(HEADER_Y + 14, logo_bottom)
    else:
        # logo centrado/derecha en su propia fila; la marca va debajo
        out["text_x"] = LM
        out["brand_y"] = logo_bottom
        out["fields_y"] = logo_bottom + 12  # marca 6 + eslogan 4 + 2
    return out

FONTS_DIR = Path(__file__).parent / "assets" / "fonts"
_FONT_FILES = {
    "inter": ("Inter-Regular.ttf", "Inter-Bold.ttf", "Inter-Italic.ttf"),
    "source_serif": ("SourceSerif4-Regular.ttf", "SourceSerif4-Bold.ttf", "SourceSerif4-It.ttf"),
    "dejavu": ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf", "DejaVuSans-Oblique.ttf"),
}


def _register_font(pdf: FPDF, key: str) -> str:
    """Register the branded TTF family on this FPDF instance (per-instance!).

    Returns the family name for set_font(); falls back to core Helvetica when
    the key is unknown or the font files are unavailable.
    """
    files = _FONT_FILES.get(key)
    if not files:
        return "Helvetica"
    try:
        for style, fname in zip(("", "B", "I"), files, strict=True):
            pdf.add_font(key, style, str(FONTS_DIR / fname))
        return key
    except Exception as exc:
        logger.warning("[PDF] fuente %s no disponible (%s); se usa Helvetica", key, exc)
        return "Helvetica"


def _logo_source(branding: PdfBranding):
    """Resolve the logo: (fpdf image source, aspect ratio) or (None, 1.0).

    Prefers the v2 base64 data-URL; falls back to the legacy logo_path file.
    """
    data_url = branding.logo.data_url or ""
    if data_url.startswith("data:image/png;base64,"):
        try:
            raw = base64.b64decode(data_url.split(",", 1)[1])
            with Image.open(BytesIO(raw)) as img:
                aspect = img.width / img.height
            return BytesIO(raw), aspect
        except Exception as exc:
            logger.warning("[PDF] logo data-URL ilegible: %s", exc)
    if branding.logo_path:
        path = Path(branding.logo_path)
        if not path.is_absolute():
            path = Path(__file__).parent.parent / branding.logo_path
        if path.exists():
            try:
                with Image.open(path) as img:
                    aspect = img.width / img.height
                return str(path), aspect
            except Exception as exc:
                logger.warning("[PDF] logo no legible: %s", exc)
    return None, 1.0


def _item_cols(opts: PdfOptions, letra: str, show_alic: bool, gross_mode: bool) -> list:
    """Resolve items-table columns as (key, width_mm, label, align).

    Hidden columns donate their width to the description column. Factura C
    (which never discriminates IVA) and gross mode collapse the net/gross
    pair into a single "Subtotal".
    """
    cols = [
        ("cod", 12, "Código", "C"),
        ("desc", 58, "Producto / Servicio", "L"),
        ("qty", 14, "Cantidad", "C"),
        ("unit", 18, "U. medida", "C"),
        ("price", 24, "Precio Unit.", "R"),
        ("bonif", 13, "% Bonif.", "C"),
        ("sub", 22, "Subtotal", "R"),
        ("alic", 12, "Alíc. IVA", "C"),
        ("total", 17, "Subtotal c/IVA", "R"),
    ]
    drop = set()
    if not opts.col_code:
        drop.add("cod")
    if not opts.col_discount:
        drop.add("bonif")
    if not opts.col_unit:
        drop.add("unit")
    if not show_alic:
        drop.add("alic")
    single_subtotal = letra == "C" or gross_mode
    if single_subtotal:
        drop.add("sub")
    freed = sum(w for k, w, _l, _a in cols if k in drop)
    kept = []
    for key, w, label, align in cols:
        if key in drop:
            continue
        if key == "desc":
            w += freed
        if key == "total" and single_subtotal:
            label = "Subtotal"
        kept.append((key, w, label, align))
    return kept


@dataclass
class _Ctx:
    """Mutable render context threaded through the section renderers."""

    branding: PdfBranding
    st: PdfStyle
    opts: PdfOptions
    font: str
    core: bool
    letra: str
    cbte_cod: str
    fiscal: bool
    doc_kind: str
    nro_comprobante: int
    fecha_cbte: str
    cae_str: str
    cae_vto_str: str
    invoice_num: str
    due_date_str: str
    doc_nro: int
    receptor_nombre: str
    receptor_cuit: str
    receptor_iva_str: str
    periodo_desde: str | None
    periodo_hasta: str | None
    alicuotas: list
    total: float
    tax_total: float
    neto: float
    line_items: list
    notes: str
    arca_data: dict
    result: dict
    cuit_emisor: str
    punto_venta: int
    cols: list
    row_h: float
    item_font: float
    alic_rate: float
    alic_display: str
    breakdown: str
    gross_mode: bool
    cur_y: float = 0.0

    def txt(self, s) -> str:
        """User-origin text, degraded to latin-1 when the font is core Helvetica."""
        s = str(s or "")
        if self.core:
            return s.encode("latin-1", "replace").decode("latin-1")
        return s


def _ensure(pdf: FPDF, c: _Ctx, needed: float) -> None:
    """Start a new page when the next block of *needed* mm would overflow."""
    if c.cur_y + needed > PAGE_BOTTOM:
        pdf.add_page()
        c.cur_y = 12


def _multicell_height(pdf: FPDF, c: _Ctx, text: str, line_h: float, size: float, style: str = "") -> float:
    """Measured height of a multi_cell without rendering it."""
    pdf.set_font(c.font, style, size)
    try:
        return float(
            pdf.multi_cell(CW, line_h, c.txt(text), dry_run=True, output="HEIGHT")
        )
    except Exception:
        # estimación conservadora si la API de dry-run cambiara
        lines = max(1, len(c.txt(text)) * 2 // 100 + 1)
        return lines * line_h


def _bottom_block_height(pdf: FPDF, c: _Ctx) -> float:
    """Total height (mm) of everything from the totals block to the footer.

    Mirrors the render math of each section so the expandido layout can
    anchor the whole block to the bottom of the page.
    """
    o = c.opts
    if c.breakdown == "total_only":
        lines = 0
    elif c.breakdown == "all":
        lines = 2 + len(c.alicuotas)  # neto + alícuotas + otros tributos
    else:
        lines = 3                     # neto + IVA + otros tributos
    height = 5 + 5 * lines + 12       # padding + líneas + caja del total

    pay_count = sum(
        1 for v in (o.pay_titular, o.pay_cbu, o.pay_alias, o.pay_banco, o.pay_medios) if v
    )
    if pay_count:
        height += 7 + 5 * pay_count + 3

    if o.show_customer_notes and c.notes:
        height += _multicell_height(pdf, c, c.notes, 4, 7.5) + 2
    if o.terms:
        height += _multicell_height(pdf, c, o.terms, 3.5, 6.5) + 2
    if c.branding.premium_msg:
        height += 7
    legends = _legal_legends(c)
    if legends:
        for legend in legends:
            height += _multicell_height(pdf, c, legend, 3.2, 6.5) + 1
        height += 1
    if c.fiscal:
        height += 0.5 + 23   # panel CAE + QR
    else:
        height += 3 + 10     # separador + nota "sin validez fiscal"
    height += 12         # footer
    return height


class _InvoicePDF(FPDF):
    """FPDF subclass that stamps a diagonal watermark on every page when set.

    ``header()`` is invoked automatically on each ``add_page()`` (including the
    first), so multi-page non-fiscal documents get the watermark on every
    sheet. It draws *behind* the body content, which uses absolute positioning
    and always resets its own colour/font/xy, so it never disturbs the layout.
    """

    watermark_text: str | None = None

    def header(self) -> None:  # fpdf2 hook, called on every add_page()
        if self.watermark_text:
            _stamp_watermark(self, self.watermark_text)


def _stamp_watermark(pdf: FPDF, text: str) -> None:
    """Draw a large, light, 45° diagonal watermark centred on the A4 page."""
    with pdf.rotation(45, x=105, y=150):
        pdf.set_font("Helvetica", "B", 58)
        pdf.set_text_color(228, 230, 236)
        pdf.set_xy(0, 138)
        pdf.cell(210, 24, text, align="C")
    # restore a sane origin for the absolute writes that follow
    pdf.set_xy(LM, HEADER_Y)


def generate_invoice_pdf(
    detail: dict,
    arca_data: dict,
    result: dict,
    cuit_emisor: str,
    branding: PdfBranding | None = None,
    *,
    fiscal: bool = True,
    doc_kind: str = "factura",
) -> bytes:
    """Return PDF bytes for an invoice with configurable branding.

    ``fiscal=True`` renders a complete AFIP comprobante (QR that resolves on
    afip.gob.ar, CAE panel, legal legends) — use it **only** when *result*
    carries a real, ARCA-granted CAE. ``fiscal=False`` gates all of that off
    and stamps a "MUESTRA / SIN VALIDEZ FISCAL" watermark, so the output can
    never be mistaken for a genuine invoice. ``doc_kind="proforma"`` also
    titles the document PRESUPUESTO / PROFORMA and drops the comprobante
    letter box (a proforma is not a comprobante and needs no CAE).
    """
    branding = branding or branding_from_env()
    st, opts = branding.style, branding.options

    # A proforma is never a fiscal comprobante.
    if doc_kind == "proforma":
        fiscal = False

    # Tolerate partial inputs on the non-fiscal paths (proforma callers may
    # omit CAE/receptor fields entirely).
    result = dict(result or {})
    arca_data = dict(arca_data or {})
    arca_data.setdefault("fecha", datetime.date.today().isoformat())
    arca_data.setdefault("punto_venta", 0)
    arca_data.setdefault("importe_total", detail.get("total", 0))

    tipo_cbte   = arca_data.get("tipo_cbte", 6)
    punto_venta = arca_data["punto_venta"]
    letra       = TIPO_CBTE_LETRA.get(tipo_cbte, "?")

    _, doc_nro = _cuit_to_doc(arca_data.get("cuit_receptor", ""))
    concepto = arca_data.get("concepto", 2)
    periodo_desde, periodo_hasta = _periodo_dates(arca_data["fecha"], concepto)

    # Filter alícuotas with importe > 0
    alicuotas = [a for a in arca_data.get("alicuotas", []) if float(a.get("importe", 0)) > 0]

    total     = float(detail.get("total", arca_data.get("importe_total", 0)))
    tax_total = float(detail.get("tax_total", arca_data.get("importe_iva", 0)))

    show_alic = {"auto": letra == "A", "show": True, "hide": False}.get(
        opts.iva_column, letra == "A"
    )
    if letra == "C":
        show_alic = False  # Factura C nunca discrimina IVA
    gross_mode = opts.prices_include_iva and letra == "B"
    # RG 5616/5614: con precios finales al consumidor no se discrimina neto/IVA
    # en los totales — el IVA contenido se informa en la leyenda de transparencia.
    breakdown = "total_only" if (letra == "C" or gross_mode) else opts.iva_breakdown

    if alicuotas:
        alic_display = ALICUOTA_LABEL.get(alicuotas[0]["id"], "21%")
        alic_rate = float(alic_display.replace(",", ".").replace("%", "")) / 100
    elif letra == "C":
        alic_rate, alic_display = 0.0, ""
    else:
        alic_rate, alic_display = 0.21, "21%"

    row_h, item_font = DENSITY_SPECS.get(st.density, DENSITY_SPECS["normal"])

    # ── PDF setup ────────────────────────────────────────────────────────────
    pdf = _InvoicePDF(orientation="P", unit="mm", format="A4")
    pdf.set_margins(LM, 10, LM)
    pdf.set_auto_page_break(auto=False)
    pdf.alias_nb_pages()
    font = _register_font(pdf, st.font)
    if not fiscal:
        pdf.watermark_text = "PRESUPUESTO" if doc_kind == "proforma" else "MUESTRA"
    pdf.add_page()

    due_date_raw = detail.get("due_date") or arca_data.get("fecha_vto") or arca_data["fecha"]
    c = _Ctx(
        branding=branding, st=st, opts=opts,
        font=font, core=font == "Helvetica",
        letra=letra,
        cbte_cod=TIPO_CBTE_COD.get(tipo_cbte, ""),
        fiscal=fiscal, doc_kind=doc_kind,
        nro_comprobante=int(result.get("nro_comprobante", 0) or 0),
        fecha_cbte=_fmt_date(arca_data["fecha"]),
        cae_str=str(result.get("cae", "")),
        cae_vto_str=_fmt_date(str(result.get("cae_vto", ""))),
        invoice_num=detail.get("invoice_number", ""),
        due_date_str=_fmt_date(str(due_date_raw)),
        doc_nro=doc_nro,
        receptor_nombre=detail.get("customer_name", ""),
        receptor_cuit=_fmt_cuit(arca_data.get("cuit_receptor", "") or ""),
        receptor_iva_str=_receptor_iva(doc_nro),
        periodo_desde=periodo_desde, periodo_hasta=periodo_hasta,
        alicuotas=alicuotas, total=total, tax_total=tax_total,
        neto=round(total - tax_total, 2),
        line_items=detail.get("line_items", []),
        notes=str(detail.get("notes", "") or ""),
        arca_data=arca_data, result=result, cuit_emisor=cuit_emisor,
        punto_venta=punto_venta,
        cols=_item_cols(opts, letra, show_alic, gross_mode),
        row_h=row_h, item_font=item_font,
        alic_rate=alic_rate, alic_display=alic_display,
        breakdown=breakdown, gross_mode=gross_mode,
    )

    _render_banner(pdf, c)
    _render_header(pdf, c)
    _render_periodo(pdf, c)
    _render_receptor(pdf, c)
    _render_items(pdf, c)
    if opts.layout == "expandido":
        # Página completa: el bloque totales→footer se ancla al pie de la hoja.
        # Si el contenido no entra, cae al flujo compacto (los _ensure paginan).
        block_h = _bottom_block_height(pdf, c)
        c.cur_y = max(c.cur_y, PAGE_BOTTOM - block_h - 1.0)  # 1 mm de holgura
    _render_totals(pdf, c)
    _render_payment(pdf, c)
    _render_notes(pdf, c)
    _render_terms(pdf, c)
    _render_premium(pdf, c)
    _render_legends(pdf, c)
    _render_cae_qr(pdf, c)
    _render_footer(pdf, c)

    return bytes(pdf.output())


# ── Section renderers ────────────────────────────────────────────────────────

def _render_banner(pdf: FPDF, c: _Ctx) -> None:
    if c.doc_kind == "proforma":
        text = "PRESUPUESTO / PROFORMA - No válido como factura"
    elif not c.fiscal:
        text = "MUESTRA - Documento sin validez fiscal"
    else:
        text = "ORIGINAL"
    pdf.set_fill_color(*c.st.primary)
    pdf.set_text_color(*C_WHITE)
    pdf.set_font(c.font, "B", 13)
    pdf.set_xy(LM, 10)
    pdf.cell(CW, 8, c.txt(text), align="C", fill=True)


def _render_header(pdf: FPDF, c: _Ctx) -> None:
    HDR_Y = HEADER_Y
    b = c.branding

    # — Left column: logo + brand + emisor —
    logo_src, aspect = _logo_source(b)
    lay = _logo_layout(b.logo, aspect if logo_src is not None else None)
    text_x = lay["text_x"]
    brand_y = lay["brand_y"]
    left_y = lay["fields_y"]
    if logo_src is not None:
        try:
            pdf.image(
                logo_src,
                x=lay["logo_x"], y=lay["logo_y"],
                w=lay["logo_w"], h=lay["logo_h"],
            )
        except Exception as exc:
            logger.warning("[PDF] logo no cargado: %s", exc)
            fallback = _logo_layout(b.logo, None)
            text_x, brand_y, left_y = (
                fallback["text_x"], fallback["brand_y"], fallback["fields_y"]
            )

    pdf.set_xy(text_x, brand_y)
    pdf.set_text_color(*c.st.primary)
    pdf.set_font(c.font, "B", 13)
    pdf.cell(LEFT_W - (text_x - LM), 6, c.txt(b.brand_name))

    pdf.set_xy(text_x, brand_y + 6)
    pdf.set_text_color(*c.st.accent)
    pdf.set_font(c.font, "", 7)
    pdf.cell(LEFT_W - (text_x - LM), 4, c.txt(b.brand_tagline))

    def _left_field(label: str, value: str) -> None:
        nonlocal left_y
        if not value:
            return
        pdf.set_xy(LM, left_y)
        pdf.set_text_color(*c.st.primary)
        pdf.set_font(c.font, "B", 7.5)
        lw = pdf.get_string_width(label + " ") + 1
        pdf.cell(lw, 5, label)
        pdf.set_text_color(*C_DARK)
        pdf.set_font(c.font, "", 7.5)
        pdf.cell(LEFT_W - lw, 5, c.txt(value))
        left_y += 5

    _left_field("Razón Social:", b.emisor_razon_social)
    _left_field("Domicilio Comercial:", b.emisor_domicilio)
    _left_field("Condición frente al IVA:", b.emisor_iva)
    _left_field("Ingresos Brutos:", b.emisor_iibb)
    _left_field("Inicio de Actividades:", b.emisor_inicio_act)

    # — Center column: letter box (a proforma is not a comprobante) —
    if c.doc_kind != "proforma":
        BOX_SIZE = 16
        box_x = CTR_X + 1
        box_y = HDR_Y + 4
        pdf.set_fill_color(*c.st.primary)
        pdf.rect(box_x, box_y, BOX_SIZE, BOX_SIZE, style="F")
        pdf.set_text_color(*C_WHITE)
        pdf.set_font(c.font, "B", 24)
        pdf.set_xy(box_x, box_y)
        pdf.cell(BOX_SIZE, BOX_SIZE, c.letra, align="C")
        pdf.set_text_color(*C_MID)
        pdf.set_font(c.font, "", 7)
        pdf.set_xy(box_x, box_y + BOX_SIZE + 1)
        pdf.cell(BOX_SIZE, 4, c.cbte_cod, align="C")

    # — Right column: comprobante data —
    right_y = HDR_Y + 1

    def _right_field(label: str, value: str) -> None:
        nonlocal right_y
        pdf.set_xy(RIGHT_X, right_y)
        pdf.set_text_color(*C_MID)
        pdf.set_font(c.font, "", 7.5)
        lw = pdf.get_string_width(label + " ") + 1
        pdf.cell(lw, 5, label)
        pdf.set_text_color(*C_DARK)
        pdf.set_font(c.font, "B", 7.5)
        pdf.cell(RIGHT_W - lw, 5, value)
        right_y += 5

    pdf.set_xy(RIGHT_X, right_y)
    pdf.set_text_color(*c.st.primary)
    pdf.set_font(c.font, "B", 16)
    pdf.cell(RIGHT_W, 9, "PRESUPUESTO" if c.doc_kind == "proforma" else "FACTURA")
    right_y += 9

    _right_field(
        "Punto de Venta:",
        f"{c.punto_venta:04d}   Comp. Nro: {c.nro_comprobante:08d}",
    )
    _right_field("Fecha de Emisión:", c.fecha_cbte)
    _right_field("CUIT:", _fmt_cuit(c.cuit_emisor))

    if c.opts.show_proforma:
        # Thin separator before proforma ref
        pdf.set_draw_color(*c.st.primary)
        pdf.set_line_width(0.2)
        pdf.line(RIGHT_X, right_y + 1, RIGHT_X + RIGHT_W, right_y + 1)
        right_y += 3
        _right_field("Factura proforma:", c.txt(c.invoice_num))

    # — Header bottom border + vertical separators —
    hdr_bottom = max(left_y, right_y, HDR_Y + 36) + 2
    pdf.set_draw_color(*c.st.primary)
    pdf.set_line_width(0.4)
    pdf.line(LM, hdr_bottom, LM + CW, hdr_bottom)
    if c.doc_kind != "proforma":
        pdf.line(CTR_X, HDR_Y, CTR_X, hdr_bottom)
    pdf.line(RIGHT_X, HDR_Y, RIGHT_X, hdr_bottom)

    c.cur_y = hdr_bottom + 1


def _render_periodo(pdf: FPDF, c: _Ctx) -> None:
    if not c.periodo_desde or not c.opts.show_periodo:
        return
    cell_w = CW / 3
    pdf.set_fill_color(*c.st.light_bg)

    pdf.set_text_color(*c.st.primary)
    pdf.set_font(c.font, "B", 7.5)
    for x, text in [
        (LM,              "Período Facturado Desde:"),
        (LM + cell_w,     "Hasta:"),
        (LM + cell_w * 2, "Fecha de Vto. para el pago:"),
    ]:
        pdf.set_xy(x, c.cur_y)
        pdf.cell(cell_w, 5, text, fill=True)
    c.cur_y += 5

    pdf.set_font(c.font, "B", 9)
    for x, text, color in [
        (LM,              c.periodo_desde, C_DARK),
        (LM + cell_w,     c.periodo_hasta, C_DARK),
        (LM + cell_w * 2, c.due_date_str,  c.st.primary),
    ]:
        pdf.set_xy(x, c.cur_y)
        pdf.set_text_color(*color)
        pdf.cell(cell_w, 5, text, fill=True)
    c.cur_y += 5

    pdf.set_draw_color(*c.st.primary)
    pdf.set_line_width(0.4)
    pdf.line(LM, c.cur_y, LM + CW, c.cur_y)
    c.cur_y += 1


def _render_receptor(pdf: FPDF, c: _Ctx) -> None:
    pdf.set_fill_color(*c.st.primary)
    pdf.set_text_color(*C_WHITE)
    pdf.set_font(c.font, "B", 7.5)
    pdf.set_xy(LM, c.cur_y)
    pdf.cell(CW, 6, "DATOS DEL RECEPTOR", fill=True)
    c.cur_y += 6

    pdf.set_fill_color(*c.st.light_bg)
    cuit_display   = "Consumidor Final" if c.doc_nro == 0 else c.receptor_cuit
    nombre_display = c.receptor_nombre or ("Consumidor Final" if c.doc_nro == 0 else "")

    def _rec_pair(x1, lbl1, val1, w1, x2, lbl2, val2, w2, y):
        for x, lbl, val, w in [(x1, lbl1, val1, w1), (x2, lbl2, val2, w2)]:
            pdf.set_xy(x, y)
            pdf.set_text_color(*c.st.primary)
            pdf.set_font(c.font, "B", 7.5)
            lw = pdf.get_string_width(lbl + " ") + 1
            pdf.cell(lw, 5, lbl, fill=True)
            pdf.set_text_color(*C_DARK)
            pdf.set_font(c.font, "", 7.5)
            pdf.cell(w - lw, 5, c.txt(val), fill=True)

    HALF = CW / 2
    _rec_pair(
        LM,        "CUIT:",              cuit_display,    HALF,
        LM + HALF, "Razón Social:",      nombre_display,  HALF,
        c.cur_y,
    )
    c.cur_y += 5
    _rec_pair(
        LM,        "Condición IVA:",     c.receptor_iva_str, HALF,
        LM + HALF, "Domicilio Comercial:", "",               HALF,
        c.cur_y,
    )
    c.cur_y += 5

    pdf.set_xy(LM, c.cur_y)
    pdf.set_text_color(*c.st.primary)
    pdf.set_font(c.font, "B", 7.5)
    lw = pdf.get_string_width("Condición de venta: ") + 1
    pdf.cell(lw, 5, "Condición de venta:", fill=True)
    pdf.set_text_color(*C_DARK)
    pdf.set_font(c.font, "", 7.5)
    pdf.cell(CW - lw, 5, "Otra", fill=True)
    c.cur_y += 5

    pdf.set_draw_color(*c.st.primary)
    pdf.set_line_width(0.5)
    pdf.line(LM, c.cur_y, LM + CW, c.cur_y)
    c.cur_y += 2


def _render_items_header(pdf: FPDF, c: _Ctx) -> None:
    pdf.set_xy(LM, c.cur_y)
    pdf.set_font(c.font, "B", 7)
    if c.st.table == "minimal":
        pdf.set_text_color(*c.st.primary)
        for _key, w, label, align in c.cols:
            pdf.cell(w, 7, label, align=align)
        pdf.set_draw_color(*c.st.primary)
        pdf.set_line_width(0.4)
        pdf.line(LM, c.cur_y + 7, LM + CW, c.cur_y + 7)
    else:
        pdf.set_fill_color(*c.st.primary)
        pdf.set_text_color(*C_WHITE)
        for _key, w, label, align in c.cols:
            pdf.cell(w, 7, label, fill=True, align=align)
    c.cur_y += 7


def _render_items(pdf: FPDF, c: _Ctx) -> None:
    _render_items_header(pdf, c)
    for idx, item in enumerate(c.line_items):
        if c.cur_y + c.row_h > ROW_CUTOFF:
            pdf.add_page()
            c.cur_y = 12
            _render_items_header(pdf, c)
        qty      = float(item.get("quantity", 1))
        rate     = float(item.get("rate", 0))
        subtotal = float(item.get("item_total", qty * rate))
        name     = item.get("name", "") or ""
        desc     = item.get("description", "") or ""
        label    = c.txt((f"{name} - {desc}" if desc else name)[:120])
        gross    = round(subtotal * (1 + c.alic_rate), 2)
        price    = rate * (1 + c.alic_rate) if c.gross_mode else rate
        total_val = subtotal if c.letra == "C" else gross
        qty_str  = f"{qty:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        values = {
            "cod": "",
            "desc": label,
            "qty": qty_str,
            "unit": "unidades",
            "price": _fmt_money(price),
            "bonif": "0,00",
            "sub": _fmt_money(subtotal),
            "alic": c.alic_display,
            "total": _fmt_money(total_val),
        }
        row_fill = c.st.table == "zebra" and idx % 2 == 1
        pdf.set_fill_color(*(c.st.light_bg if row_fill else C_WHITE))
        pdf.set_text_color(*C_DARK)
        pdf.set_font(c.font, "", c.item_font)
        # clip the description to its column so it never bleeds into Cantidad
        desc_w = next(w for k, w, _l, _a in c.cols if k == "desc") - 2
        while label and pdf.get_string_width(label) > desc_w:
            label = label[:-1]
        values["desc"] = label
        pdf.set_xy(LM, c.cur_y)
        for key, w, _label, align in c.cols:
            pdf.cell(w, c.row_h, values[key], fill=row_fill, align=align)
        if c.st.table == "rules":
            pdf.set_draw_color(215, 215, 215)
            pdf.set_line_width(0.2)
            pdf.line(LM, c.cur_y + c.row_h, LM + CW, c.cur_y + c.row_h)
        c.cur_y += c.row_h


def _render_totals(pdf: FPDF, c: _Ctx) -> None:
    c.cur_y += 5
    TOT_LBL = 55
    TOT_VAL = 25
    tot_x = LM + CW - (TOT_LBL + TOT_VAL)
    est = 14 if c.breakdown == "total_only" else 14 + 5 * (3 + len(c.alicuotas))
    _ensure(pdf, c, est)

    def _line(label: str, value: str) -> None:
        pdf.set_xy(tot_x, c.cur_y)
        pdf.set_text_color(*C_MID)
        pdf.set_font(c.font, "", 8)
        pdf.cell(TOT_LBL, 5, label, align="R")
        pdf.cell(TOT_VAL, 5, value, align="R")
        c.cur_y += 5

    if c.breakdown != "total_only":
        pdf.set_font(c.font, "", 8)
        pdf.set_text_color(*C_MID)
        pdf.set_xy(LM, c.cur_y)
        pdf.cell(90, 5, "Importe Otros Tributos:   $0,00")

        pdf.set_xy(tot_x, c.cur_y)
        pdf.set_text_color(*c.st.primary)
        pdf.set_font(c.font, "B", 8)
        pdf.cell(TOT_LBL, 5, "Importe Neto Gravado:", align="R")
        pdf.set_text_color(*C_DARK)
        pdf.cell(TOT_VAL, 5, _fmt_money(c.neto), align="R")
        c.cur_y += 5

        if c.breakdown == "all":
            for alic in c.alicuotas:
                _line(_alicuota_label(alic["id"]), _fmt_money(float(alic["importe"])))
        else:
            _line("IVA:", _fmt_money(c.tax_total))

        _line("Importe Otros Tributos:", "$0,00")

    # Grand total box
    pdf.set_fill_color(*c.st.primary)
    pdf.rect(tot_x, c.cur_y, TOT_LBL + TOT_VAL, 8, style="F")
    pdf.set_text_color(*C_WHITE)
    pdf.set_font(c.font, "B", 10)
    pdf.set_xy(tot_x, c.cur_y)
    pdf.cell(TOT_LBL, 8, "Importe Total:", align="R")
    pdf.cell(TOT_VAL, 8, _fmt_money(c.total), align="R")
    c.cur_y += 8 + 4


def _render_payment(pdf: FPDF, c: _Ctx) -> None:
    o = c.opts
    pairs = [
        (label, value)
        for label, value in (
            ("Titular:", o.pay_titular),
            ("CBU:", o.pay_cbu),
            ("Alias:", o.pay_alias),
            ("Banco:", o.pay_banco),
            ("Medios de pago:", o.pay_medios),
        )
        if value
    ]
    if not pairs:
        return
    height = 7 + 5 * len(pairs)
    _ensure(pdf, c, height + 4)
    pdf.set_fill_color(*c.st.light_bg)
    pdf.rect(LM, c.cur_y, CW, height, style="F")
    pdf.set_xy(LM + 2, c.cur_y + 1)
    pdf.set_text_color(*c.st.primary)
    pdf.set_font(c.font, "B", 8)
    pdf.cell(CW - 4, 5, "Datos para el pago")
    y = c.cur_y + 6
    for label, value in pairs:
        pdf.set_xy(LM + 2, y)
        pdf.set_text_color(*c.st.primary)
        pdf.set_font(c.font, "B", 7.5)
        lw = pdf.get_string_width(label + " ") + 1
        pdf.cell(lw, 5, label)
        pdf.set_text_color(*C_DARK)
        pdf.set_font(c.font, "", 7.5)
        pdf.cell(CW - 4 - lw, 5, c.txt(value))
        y += 5
    c.cur_y += height + 3


def _render_notes(pdf: FPDF, c: _Ctx) -> None:
    if not (c.opts.show_customer_notes and c.notes):
        return
    _ensure(pdf, c, 12)
    pdf.set_text_color(*C_DARK)
    pdf.set_font(c.font, "", 7.5)
    pdf.set_xy(LM, c.cur_y)
    pdf.multi_cell(CW, 4, c.txt(c.notes))
    c.cur_y = pdf.get_y() + 2


def _render_terms(pdf: FPDF, c: _Ctx) -> None:
    if not c.opts.terms:
        return
    _ensure(pdf, c, 12)
    pdf.set_text_color(*C_MID)
    pdf.set_font(c.font, "", 6.5)
    pdf.set_xy(LM, c.cur_y)
    pdf.multi_cell(CW, 3.5, c.txt(c.opts.terms))
    c.cur_y = pdf.get_y() + 2


def _render_premium(pdf: FPDF, c: _Ctx) -> None:
    if not c.branding.premium_msg:
        return
    _ensure(pdf, c, 8)
    pdf.set_text_color(*c.st.primary)
    pdf.set_font(c.font, "I", 9)
    pdf.set_xy(LM, c.cur_y)
    pdf.cell(CW, 7, c.txt(f'"{c.branding.premium_msg}"'), align="C")
    c.cur_y += 7


def _legal_legends(c: _Ctx) -> list[str]:
    """Mandatory-ish legends by letra + emisor condition (opt-out via options)."""
    if not c.fiscal or not c.opts.legal_legends:
        return []
    legends = []
    if c.letra == "B" and c.tax_total > 0:
        legends.append(
            "Régimen de Transparencia Fiscal al Consumidor (Ley 27.743) - "
            f"IVA Contenido: {_fmt_money(c.tax_total)} - "
            "Otros Impuestos Nacionales Indirectos: $0,00"
        )
    if "monotribut" in (c.branding.emisor_iva or "").lower():
        legends.append(
            "Comprobante emitido por un sujeto adherido al Régimen Simplificado "
            "para Pequeños Contribuyentes (Monotributo)."
        )
    if c.letra in ("B", "C"):
        legends.append(
            "Defensa del Consumidor: www.argentina.gob.ar/defensadelconsumidor"
            " - Tel: 0800-666-1518"
        )
    return legends


def _render_legends(pdf: FPDF, c: _Ctx) -> None:
    legends = _legal_legends(c)
    if not legends:
        return
    _ensure(pdf, c, 5 * len(legends) + 2)
    pdf.set_text_color(*C_MID)
    pdf.set_font(c.font, "", 6.5)
    for legend in legends:
        pdf.set_xy(LM, c.cur_y)
        pdf.multi_cell(CW, 3.2, c.txt(legend))
        c.cur_y = pdf.get_y() + 1
    c.cur_y += 1


def _render_cae_qr(pdf: FPDF, c: _Ctx) -> None:
    if not c.fiscal:
        # No AFIP QR / CAE on non-fiscal output — a plain disclaimer instead.
        _ensure(pdf, c, 14)
        pdf.set_draw_color(*c.st.accent)
        pdf.set_line_width(0.8)
        pdf.line(LM, c.cur_y, LM + CW, c.cur_y)
        c.cur_y += 3
        pdf.set_text_color(*C_MID)
        pdf.set_font(c.font, "I", 7)
        pdf.set_xy(LM, c.cur_y)
        note = (
            "Este documento no es un comprobante fiscal y no reemplaza a la "
            "factura electrónica exigida por ARCA/AFIP."
        )
        pdf.multi_cell(CW, 3.5, c.txt(note))
        c.cur_y = pdf.get_y() + 2
        return
    _ensure(pdf, c, 36)  # panel CAE (23.5) + footer (12): viajan juntos
    pdf.set_draw_color(*c.st.accent)
    pdf.set_line_width(0.8)
    pdf.line(LM, c.cur_y, LM + CW, c.cur_y)
    c.cur_y += 0.5

    pdf.set_fill_color(*c.st.light_bg)
    pdf.rect(LM, c.cur_y, CW, 22, style="F")

    try:
        qr_url = _build_qr_url(c.arca_data, c.result, c.cuit_emisor)
        pdf.image(BytesIO(_build_qr_png(qr_url)), x=LM + 2, y=c.cur_y + 2, w=18, h=18)
    except Exception as exc:
        logger.warning("[PDF] QR no generado: %s", exc)

    pdf.set_text_color(*C_DARK)
    pdf.set_font(c.font, "", 7.5)
    pdf.set_xy(LM + 23, c.cur_y + 3)
    pdf.cell(80, 4, f"Pág. {pdf.page_no()}/{{nb}}", align="C")
    pdf.set_font(c.font, "B", 7.5)
    pdf.set_xy(LM + 23, c.cur_y + 8)
    pdf.cell(80, 4, "Comprobante Autorizado")
    pdf.set_font(c.font, "I", 6.5)
    pdf.set_xy(LM + 23, c.cur_y + 13)
    pdf.cell(
        80, 4,
        "Esta Administración Federal no se responsabiliza por los datos ingresados en el detalle de la operación",
    )

    pdf.set_text_color(*c.st.primary)
    pdf.set_font(c.font, "B", 8)
    pdf.set_xy(LM + 120, c.cur_y + 5)
    pdf.cell(70, 5, f"CAE N°: {c.cae_str}", align="R")
    pdf.set_text_color(*C_MID)
    pdf.set_font(c.font, "", 7.5)
    pdf.set_xy(LM + 120, c.cur_y + 11)
    pdf.cell(70, 5, f"Fecha de Vto. de CAE: {c.cae_vto_str}", align="R")
    c.cur_y += 23


def _render_footer(pdf: FPDF, c: _Ctx) -> None:
    _ensure(pdf, c, 13)
    b = c.branding
    pdf.set_fill_color(*c.st.primary)
    pdf.rect(LM, c.cur_y, CW, 12, style="F")

    pdf.set_text_color(*C_WHITE)
    pdf.set_font(c.font, "B", 8)
    pdf.set_xy(LM + 2, c.cur_y + 1)
    pdf.cell(90, 5, c.txt(b.emisor_nombre))

    pdf.set_text_color(*c.st.light_bg)
    pdf.set_font(c.font, "", 7)
    pdf.set_xy(LM + 2, c.cur_y + 6)
    parts = []
    if b.emisor_domicilio:
        parts.append(b.emisor_domicilio)
    if b.contact_tel:
        parts.append(f"Tel: {b.contact_tel}")
    pdf.cell(100, 4, c.txt("   ".join(parts)))

    # light_bg (not accent) so the contact stays legible on every preset's
    # primary fill — with moderno/minimal the accent hue ~= the primary hue
    pdf.set_text_color(*c.st.light_bg)
    pdf.set_font(c.font, "", 7)
    pdf.set_xy(LM + 105, c.cur_y + 2)
    pdf.cell(83, 4, c.txt(b.contact_email), align="R")
    pdf.set_xy(LM + 105, c.cur_y + 6)
    pdf.cell(83, 4, c.txt(b.contact_web), align="R")

