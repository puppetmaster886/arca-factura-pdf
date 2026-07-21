"""arca-factura-pdf — render Argentine invoice / proforma PDFs (ARCA/AFIP layout).

Pure ``dict`` in → PDF ``bytes`` out. No network, no database, no secrets.

Quickstart
----------
>>> from factura_pdf import generate_invoice_pdf, sample_invoice
>>> detail, arca_data, result, cuit = sample_invoice("productos")
>>> pdf_bytes = generate_invoice_pdf(detail, arca_data, result, cuit)   # fiscal
>>> muestra = generate_invoice_pdf(detail, arca_data, result, cuit, fiscal=False)

``fiscal=True`` (default) draws the full AFIP comprobante (QR + CAE) and must
only be used with a real, ARCA-granted CAE in ``result``. ``fiscal=False`` and
``doc_kind="proforma"`` produce a watermarked, non-fiscal document.

Made by FactuARCA — https://factuarca.app
"""
from .generator import (
    PRESETS,
    PdfBranding,
    PdfLogo,
    PdfOptions,
    PdfStyle,
    branding_from_dict,
    branding_from_env,
    generate_invoice_pdf,
)
from .logo import (
    LogoValidationError,
    build_palette,
    contrast_ratio,
    extract_palettes,
    normalize_logo,
)
from .samples import sample_invoice
from .schema import PdfBrandingModel

__version__ = "0.1.0"

__all__ = [
    "generate_invoice_pdf",
    "PdfBranding",
    "PdfStyle",
    "PdfLogo",
    "PdfOptions",
    "PRESETS",
    "branding_from_dict",
    "branding_from_env",
    "sample_invoice",
    "normalize_logo",
    "extract_palettes",
    "build_palette",
    "contrast_ratio",
    "LogoValidationError",
    "PdfBrandingModel",
    "__version__",
]
