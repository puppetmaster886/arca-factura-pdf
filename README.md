# arca-factura-pdf

**Generá el PDF de una factura o presupuesto argentino (layout ARCA/AFIP) desde Python — sin depender de ningún servicio externo.**

[![PyPI](https://img.shields.io/pypi/v/arca-factura-pdf.svg)](https://pypi.org/project/arca-factura-pdf/)
[![Python](https://img.shields.io/pypi/pyversions/arca-factura-pdf.svg)](https://pypi.org/project/arca-factura-pdf/)
[![CI](https://github.com/puppetmaster886/arca-factura-pdf/actions/workflows/ci.yml/badge.svg)](https://github.com/puppetmaster886/arca-factura-pdf/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

`arca-factura-pdf` toma un `dict` con los datos del comprobante y devuelve los **bytes de un PDF** con el diseño de una factura electrónica argentina: encabezado del emisor, letra (A/B/C), tabla de ítems, discriminación de IVA, totales, **QR y CAE** de AFIP/ARCA, y las leyendas legales. Es puro `dict → bytes`: **sin red, sin base de datos, sin secretos.**

> Extraído del motor de facturación de **[FactuARCA](https://factuarca.app)** — el servicio que automatiza la emisión de facturas ARCA desde Zoho Books. Este paquete es la capa de renderizado, liberada como open source.

## Por qué

Emitir el CAE contra el web service de ARCA (WSFEv1) es solo la mitad del trabajo: después hay que **entregarle al cliente un PDF que parezca una factura de verdad**, con el QR que resuelve en `afip.gob.ar`, el bloque de CAE, la letra correcta y las leyendas de la RG vigente. Ese layout es tedioso de reproducir. Esta librería ya lo tiene resuelto y probado en producción.

- ✅ Facturas **A / B / C** (RI, IVA discriminado o no según corresponde — la C nunca discrimina IVA).
- ✅ **QR de AFIP** (payload oficial base64 → `https://www.afip.gob.ar/fe/qr/?p=…`) y panel de **CAE + vencimiento**.
- ✅ Conceptos **productos / servicios / ambos** (período facturado para servicios).
- ✅ **Branding configurable**: logo, presets de color con contraste WCAG garantizado, tipografías embebidas, densidad, columnas, datos de pago, notas, términos.
- ✅ Facturas largas que **paginan** solas (re-render del header de tabla, `Pág. n/m`).
- ✅ Modo **no-fiscal**: proforma / presupuesto y "muestra" con marca de agua — imposible de confundir con un comprobante válido.
- ✅ Solo `fpdf2` + `qrcode` + `Pillow` (+ `pydantic` para validación opcional). Sin dependencias pesadas.

## Instalación

```bash
pip install arca-factura-pdf
```

## Uso

### 1) Factura fiscal (con un CAE real)

Usá `fiscal=True` (el default) **solo** cuando ya obtuviste un CAE real de ARCA — el `result` debe traer `cae`, `cae_vto` y `nro_comprobante`.

```python
from factura_pdf import generate_invoice_pdf

detail = {
    "invoice_number": "0001-00000123",
    "customer_name": "Distribuidora El Faro S.R.L.",
    "total": 24360.00,
    "tax_total": 3360.00,
    "line_items": [
        {"name": "Resma A4 75g", "quantity": 10, "rate": 1000, "item_total": 10000},
        {"name": "Harina 000",  "quantity": 2,  "rate": 5000, "item_total": 10000},
    ],
}
arca_data = {
    "tipo_cbte": 1,            # 1=A, 6=B, 11=C
    "punto_venta": 3,
    "fecha": "2026-07-01",
    "cuit_receptor": "30712345676",
    "importe_total": 24360.00,
    "importe_iva": 3360.00,
    "concepto": 1,             # 1=productos, 2=servicios, 3=ambos
    "alicuotas": [{"id": 5, "base_imp": 21000.0, "importe": 3360.0}],  # id 5 = 21%
}
result = {"cae": 76123456789012, "cae_vto": "20260711", "nro_comprobante": 123}

pdf_bytes = generate_invoice_pdf(detail, arca_data, result, cuit_emisor="20111111112")
open("factura.pdf", "wb").write(pdf_bytes)
```

> ⚠️ **Este paquete no emite el CAE.** Solo dibuja el PDF. El CAE lo tenés que obtener vos contra WSFEv1 (o dejar que **[FactuARCA](https://factuarca.app)** lo haga por vos).

### 2) Proforma / presupuesto (no fiscal)

```python
from factura_pdf import generate_invoice_pdf

pdf_bytes = generate_invoice_pdf(
    detail, arca_data, result={}, cuit_emisor="20111111112",
    doc_kind="proforma",          # título "PRESUPUESTO", sin letra ni CAE
)
```

### 3) "Muestra" de diseño (factura con marca de agua)

```python
pdf_bytes = generate_invoice_pdf(
    detail, arca_data, result, cuit_emisor="20111111112",
    fiscal=False,                 # marca de agua "MUESTRA", sin QR ni CAE
)
```

### Datos de ejemplo listos para probar

```python
from factura_pdf import generate_invoice_pdf, sample_invoice

for kind in ("servicios", "productos", "larga"):
    detail, arca_data, result, cuit = sample_invoice(kind)
    open(f"{kind}.pdf", "wb").write(generate_invoice_pdf(detail, arca_data, result, cuit))
```

## Branding

```python
from factura_pdf import PdfBranding, branding_from_dict, generate_invoice_pdf

branding = branding_from_dict({
    "brand_name": "Mi Estudio",
    "emisor_nombre": "Juan Pérez",
    "emisor_razon_social": "Juan Pérez",
    "emisor_iva": "IVA Responsable Inscripto",
    "style": {"preset": "moderno"},      # clasico | moderno | minimal | corporativo | elegante | custom
    "options": {"iva_breakdown": "all", "layout": "expandido"},
})
pdf_bytes = generate_invoice_pdf(detail, arca_data, result, cuit, branding=branding)
```

- **Presets**: `clasico`, `moderno`, `minimal`, `corporativo`, `elegante`. Cada primario mantiene ≥4.5 de contraste WCAG (verificado por tests).
- **Custom**: pasá `style.colors` (`{"primary": "#2456e6", ...}`) y `style.font` (`inter`, `source_serif`, `dejavu`, `helvetica`).
- **Logo**: `normalize_logo()` valida y normaliza a PNG ≤512px; `extract_palettes()` te sugiere paletas con contraste garantizado a partir del logo.
- Validación estricta opcional del doc de branding con `PdfBrandingModel` (Pydantic).

## Seguridad y uso responsable

El modo `fiscal=True` produce un PDF con QR y CAE que **parece** una factura oficial. Usalo únicamente con un CAE real y válido. Para previsualizaciones, plantillas o presupuestos usá `fiscal=False` / `doc_kind="proforma"`: esos modos estampan una marca de agua y omiten el QR/CAE, de modo que el documento nunca puede pasar por un comprobante fiscal genuino.

## Fuentes incluidas

Se embeben familias tipográficas con licencias permisivas (redistribuibles): **Inter** (SIL OFL 1.1), **Source Serif 4** (SIL OFL 1.1) y **DejaVu Sans** (Bitstream Vera / dominio público). Sus licencias están en [`factura_pdf/assets/fonts/`](factura_pdf/assets/fonts/). El core Helvetica de fpdf2 se usa como fallback.

## Desarrollo

```bash
pip install -e ".[dev]"
pytest
ruff check .
```

## Licencia

[MIT](LICENSE) © FactuARCA. Las fuentes embebidas conservan sus propias licencias.

---

<p align="center">
  Hecho por <a href="https://factuarca.app"><b>FactuARCA</b></a> — facturá en ARCA automáticamente desde Zoho Books.<br>
  <sub>¿Cansado de emitir CAEs a mano? <a href="https://factuarca.app">Probá FactuARCA</a>.</sub>
</p>
