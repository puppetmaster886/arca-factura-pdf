"""Strict Pydantic schema for ``tenants.pdf_branding`` (API boundary).

Counterpart of the tolerant parsing in ``src/pdf_generator.py``: the API
rejects anything malformed with a 422 before it reaches the DB, while the
renderer degrades gracefully on whatever is already stored. A flat v1 doc
(only the legacy text keys) validates unchanged.
"""
from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_DOC_BYTES = 400_000  # serialized doc cap: the base64 logo lives inside
_HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
_DATA_URL_RE = re.compile(r"^data:image/png;base64,[A-Za-z0-9+/=]+$")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PdfColorsModel(_Strict):
    primary: str | None = None
    accent: str | None = None
    light_bg: str | None = None

    @field_validator("primary", "accent", "light_bg")
    @classmethod
    def _hex(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not _HEX_RE.match(v):
            raise ValueError(f"color inválido {v!r}: se espera formato #rrggbb")
        return v.lower()


class PdfLogoModel(_Strict):
    data_url: str | None = None  # "" clears the logo
    position: Literal["left", "center", "right"] | None = None
    size: Literal["s", "m", "l"] | None = None

    @field_validator("data_url")
    @classmethod
    def _png_data_url(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return v
        if not _DATA_URL_RE.match(v):
            raise ValueError("data_url debe ser un PNG en data-URL base64")
        return v


class PdfStyleModel(_Strict):
    preset: Literal["clasico", "moderno", "minimal", "corporativo", "elegante", "custom"] | None = None
    colors: PdfColorsModel | None = None
    font: Literal["helvetica", "inter", "source_serif", "dejavu"] | None = None
    table: Literal["zebra", "rules", "minimal"] | None = None
    density: Literal["compact", "normal", "airy"] | None = None


class PdfColumnsModel(_Strict):
    code: bool | None = None
    discount: bool | None = None
    unit: bool | None = None


class PdfPaymentModel(_Strict):
    titular: str | None = Field(None, max_length=100)
    cbu: str | None = Field(None, max_length=100)
    alias: str | None = Field(None, max_length=100)
    banco: str | None = Field(None, max_length=100)
    medios: str | None = Field(None, max_length=200)


class PdfOptionsModel(_Strict):
    iva_breakdown: Literal["all", "main", "total_only"] | None = None
    iva_column: Literal["auto", "show", "hide"] | None = None
    prices_include_iva: bool | None = None
    legal_legends: bool | None = None
    show_customer_notes: bool | None = None
    show_proforma: bool | None = None
    show_periodo: bool | None = None
    layout: Literal["expandido", "compacto"] | None = None
    columns: PdfColumnsModel | None = None
    payment: PdfPaymentModel | None = None
    terms: str | None = Field(None, max_length=2000)


class PdfBrandingModel(_Strict):
    v: Literal[2] | None = None
    brand_name: str | None = Field(None, max_length=500)
    brand_tagline: str | None = Field(None, max_length=500)
    emisor_nombre: str | None = Field(None, max_length=500)
    emisor_razon_social: str | None = Field(None, max_length=500)
    emisor_domicilio: str | None = Field(None, max_length=500)
    emisor_iibb: str | None = Field(None, max_length=500)
    emisor_inicio_act: str | None = Field(None, max_length=500)
    emisor_iva: str | None = Field(None, max_length=500)
    contact_tel: str | None = Field(None, max_length=500)
    contact_email: str | None = Field(None, max_length=500)
    contact_web: str | None = Field(None, max_length=500)
    premium_msg: str | None = Field(None, max_length=300)
    logo_path: str | None = Field(None, max_length=500)
    logo: PdfLogoModel | None = None
    style: PdfStyleModel | None = None
    options: PdfOptionsModel | None = None

    @model_validator(mode="after")
    def _size_cap(self) -> PdfBrandingModel:
        doc = json.dumps(self.model_dump(exclude_none=True))
        if len(doc.encode()) > MAX_DOC_BYTES:
            raise ValueError(
                f"pdf_branding supera el máximo de {MAX_DOC_BYTES} bytes"
            )
        return self
