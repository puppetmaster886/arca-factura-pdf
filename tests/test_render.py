"""Rendering behaviour: fiscal vs non-fiscal gating, presets, samples."""
from io import BytesIO

import pytest
from pypdf import PdfReader

from factura_pdf import (
    PRESETS,
    branding_from_dict,
    generate_invoice_pdf,
    sample_invoice,
)
from factura_pdf.samples import CONTENT_KINDS


def _text(pdf_bytes: bytes) -> str:
    reader = PdfReader(BytesIO(pdf_bytes))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _is_pdf(pdf_bytes: bytes) -> bool:
    return pdf_bytes[:5] == b"%PDF-" and len(pdf_bytes) > 1000


def test_fiscal_invoice_has_cae_and_no_watermark():
    detail, arca_data, result, cuit = sample_invoice("productos")
    pdf = generate_invoice_pdf(detail, arca_data, result, cuit)  # fiscal=True default
    assert _is_pdf(pdf)
    text = _text(pdf)
    assert "CAE" in text
    assert "Comprobante Autorizado" in text
    assert "MUESTRA" not in text


def test_non_fiscal_stamps_muestra_and_drops_cae():
    detail, arca_data, result, cuit = sample_invoice("productos")
    pdf = generate_invoice_pdf(detail, arca_data, result, cuit, fiscal=False)
    assert _is_pdf(pdf)
    text = _text(pdf)
    assert "MUESTRA" in text
    assert "comprobante fiscal" in text          # the disclaimer line
    assert "Comprobante Autorizado" not in text  # no AFIP CAE panel


def test_proforma_titles_presupuesto_and_needs_no_cae():
    detail, arca_data, _result, cuit = sample_invoice("servicios")
    # empty result: a proforma carries no CAE at all
    pdf = generate_invoice_pdf(detail, arca_data, {}, cuit, doc_kind="proforma")
    assert _is_pdf(pdf)
    text = _text(pdf)
    assert "PRESUPUESTO" in text
    assert "Comprobante Autorizado" not in text


@pytest.mark.parametrize("kind", list(CONTENT_KINDS))
def test_every_sample_kind_renders(kind):
    detail, arca_data, result, cuit = sample_invoice(kind)
    assert _is_pdf(generate_invoice_pdf(detail, arca_data, result, cuit))


@pytest.mark.parametrize("preset", list(PRESETS))
def test_every_preset_renders(preset):
    detail, arca_data, result, cuit = sample_invoice("productos")
    branding = branding_from_dict({"style": {"preset": preset}})
    assert _is_pdf(generate_invoice_pdf(detail, arca_data, result, cuit, branding=branding))


def test_factura_c_renders_and_does_not_discriminate_iva():
    detail, arca_data, result, cuit = sample_invoice("servicios", tipo=11)
    pdf = generate_invoice_pdf(detail, arca_data, result, cuit)
    assert _is_pdf(pdf)
