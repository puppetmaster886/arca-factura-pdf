"""Canned invoice fixtures shared by the PDF preview endpoint, tests and POCs.

Each kind exercises a distinct rendering path:

- ``b_servicios``   — Factura B, concepto servicios (the INV-004370 data that
                      ``poc/poc_pdf.py`` has always used; its QR assertions
                      depend on these exact values — do not edit them).
- ``a_productos``   — Factura A, productos, two alícuotas (21% + 10,5%), CUIT.
- ``c_monotributo`` — Factura C: no IVA discrimination at all.
- ``b_larga``       — Factura B with 45 items to force multi-page layout.
- ``aleatoria``     — randomized invoice on every call (tipo, items, IVA,
                      receptor); used by the preview's «probar con otra
                      factura» flow and the local playground.

Builders return fresh objects on every call so callers may mutate freely.
"""
from __future__ import annotations

import random

CUIT_EMISOR = "20000000001"  # obviously-fake sample emisor CUIT

SAMPLE_KINDS = ("b_servicios", "a_productos", "c_monotributo", "b_larga", "aleatoria")


def _b_servicios():
    detail = {
        "invoice_number": "INV-004370",
        "date": "2026-05-16",
        "due_date": "2026-06-07",
        "customer_name": "Consumidor Final",
        "total": 1.21,
        "tax_total": 0.21,
        "notes": "Gracias por confiar en nosotros.",
        "line_items": [
            {
                "name": "Servicio de prueba",
                "description": "",
                "quantity": 1.0,
                "rate": 1.00,
                "item_total": 1.00,
            }
        ],
        "custom_fields": [],
    }
    arca_data = {
        "tipo_cbte": 6,
        "punto_venta": 1,
        "fecha": "2026-05-16",
        "fecha_vto": "2026-06-07",
        "cuit_receptor": "28861222",
        "importe_neto": 1.00,
        "importe_iva": 0.21,
        "importe_total": 1.21,
        "concepto": 2,
        "alicuotas": [{"id": 5, "base_imp": 1.00, "importe": 0.21}],
    }
    result = {"cae": 86205771799091, "cae_vto": "20260526", "nro_comprobante": 1}
    return detail, arca_data, result


def _a_productos():
    detail = {
        "invoice_number": "INV-000871",
        "date": "2026-07-01",
        "due_date": "2026-07-31",
        "customer_name": "Distribuidora El Faro S.R.L.",
        "total": 24360.00,
        "tax_total": 3360.00,
        "notes": "Entrega en depósito central.",
        "line_items": [
            {
                "name": "Resma A4 75g",
                "description": "Caja x10 resmas",
                "quantity": 10.0,
                "rate": 1000.00,
                "item_total": 10000.00,
            },
            {
                "name": "Harina 000",
                "description": "Bolsa 25 kg (alícuota reducida)",
                "quantity": 2.0,
                "rate": 5000.00,
                "item_total": 10000.00,
            },
            {
                "name": "Cinta de embalaje",
                "description": "",
                "quantity": 5.0,
                "rate": 200.00,
                "item_total": 1000.00,
            },
        ],
        "custom_fields": [],
    }
    arca_data = {
        "tipo_cbte": 1,
        "punto_venta": 3,
        "fecha": "2026-07-01",
        "fecha_vto": "2026-07-31",
        "cuit_receptor": "30712345676",
        "importe_neto": 21000.00,
        "importe_iva": 3360.00,
        "importe_total": 24360.00,
        "concepto": 1,
        "alicuotas": [
            {"id": 5, "base_imp": 11000.00, "importe": 2310.00},
            {"id": 4, "base_imp": 10000.00, "importe": 1050.00},
        ],
    }
    result = {"cae": 76123456789012, "cae_vto": "20260711", "nro_comprobante": 871}
    return detail, arca_data, result


def _c_monotributo():
    detail = {
        "invoice_number": "INV-000042",
        "date": "2026-07-01",
        "due_date": "2026-07-15",
        "customer_name": "María López",
        "total": 50000.00,
        "tax_total": 0.00,
        "line_items": [
            {
                "name": "Honorarios profesionales",
                "description": "Asesoramiento julio 2026",
                "quantity": 1.0,
                "rate": 50000.00,
                "item_total": 50000.00,
            }
        ],
        "custom_fields": [],
    }
    arca_data = {
        "tipo_cbte": 11,
        "punto_venta": 1,
        "fecha": "2026-07-01",
        "fecha_vto": "2026-07-15",
        "cuit_receptor": "",
        "importe_neto": 50000.00,
        "importe_iva": 0.00,
        "importe_total": 50000.00,
        "concepto": 2,
        "alicuotas": [],
    }
    result = {"cae": 71987654321098, "cae_vto": "20260711", "nro_comprobante": 42}
    return detail, arca_data, result


def _b_larga():
    line_items = []
    neto = 0.0
    for i in range(45):
        rate = round(80 + 7 * i, 2)
        line_items.append(
            {
                "name": f"Artículo {i + 1:02d}",
                "description": "Descripción extendida del artículo para probar el corte de línea"
                if i % 9 == 0
                else "",
                "quantity": 1.0,
                "rate": rate,
                "item_total": rate,
            }
        )
        neto += rate
    neto = round(neto, 2)
    iva = round(neto * 0.21, 2)
    total = round(neto + iva, 2)
    detail = {
        "invoice_number": "INV-001200",
        "date": "2026-07-01",
        "due_date": "2026-07-31",
        "customer_name": "Comercial del Sur S.A.",
        "total": total,
        "tax_total": iva,
        "line_items": line_items,
        "custom_fields": [],
    }
    arca_data = {
        "tipo_cbte": 6,
        "punto_venta": 1,
        "fecha": "2026-07-01",
        "fecha_vto": "2026-07-31",
        "cuit_receptor": "",
        "importe_neto": neto,
        "importe_iva": iva,
        "importe_total": total,
        "concepto": 1,
        "alicuotas": [{"id": 5, "base_imp": neto, "importe": iva}],
    }
    result = {"cae": 73456789012345, "cae_vto": "20260711", "nro_comprobante": 1200}
    return detail, arca_data, result


_RND_ITEMS = [
    ("Consultoría técnica", "Relevamiento y diagnóstico"),
    ("Desarrollo de software", "Sprint quincenal"),
    ("Resma A4 75g", "Caja x10 resmas"),
    ("Servicio de logística", ""),
    ("Honorarios profesionales", "Asesoramiento mensual"),
    ("Mantenimiento de equipos", "Visita técnica"),
    ("Harina 000", "Bolsa 25 kg"),
    ("Diseño gráfico", "Piezas para redes"),
    ("Alquiler de sala", "Jornada completa"),
    ("Capacitación in-company", "Taller de 4 horas"),
    ("Insumos de librería", ""),
    ("Soporte técnico remoto", "Bolsa de horas"),
]

_RND_CLIENTES = [
    "Distribuidora El Faro S.R.L.", "María López", "Comercial del Sur S.A.",
    "Estudio Contable Rivas", "Panadería La Espiga", "Juan Pérez",
    "Tecno Insumos S.A.", "Consumidor Final",
]

_RND_NOTAS = [
    "", "", "Gracias por su compra.", "Entrega coordinada con depósito.",
    "Incluye garantía de 6 meses.",
]


def _aleatoria(tipos: list[int] | None = None):
    """Randomized-but-consistent invoice: totals always reconcile.

    ``tipos`` acota los tipos de comprobante sorteados (p. ej. [11] para un
    emisor monotributo). Por defecto mezcla A, B y C.
    """
    rnd = random.Random()
    tipo = rnd.choice(tipos or [1, 6, 6, 11])
    n_items = rnd.randint(1, 12)

    line_items = []
    neto = 0.0
    for _ in range(n_items):
        name, desc = rnd.choice(_RND_ITEMS)
        qty = float(rnd.choice([1, 1, 2, 3, 5, 10]))
        rate = round(rnd.uniform(500, 60000), 2)
        item_total = round(qty * rate, 2)
        line_items.append(
            {"name": name, "description": desc, "quantity": qty,
             "rate": rate, "item_total": item_total}
        )
        neto += item_total
    neto = round(neto, 2)

    if tipo == 11:
        alicuotas = []
    elif tipo == 1 and n_items >= 2 and rnd.random() < 0.5:
        # Factura A con mezcla 21% + 10,5%
        half = max(1, n_items // 2)
        base21 = round(sum(i["item_total"] for i in line_items[:half]), 2)
        base105 = round(neto - base21, 2)
        alicuotas = [
            {"id": 5, "base_imp": base21, "importe": round(base21 * 0.21, 2)},
            {"id": 4, "base_imp": base105, "importe": round(base105 * 0.105, 2)},
        ]
    else:
        alicuotas = [{"id": 5, "base_imp": neto, "importe": round(neto * 0.21, 2)}]

    iva = round(sum(a["importe"] for a in alicuotas), 2)
    total = round(neto + iva, 2)

    if tipo == 1:
        cuit_receptor = "30" + "".join(str(rnd.randint(0, 9)) for _ in range(9))
        cliente = rnd.choice([c for c in _RND_CLIENTES if c != "Consumidor Final"])
    elif tipo == 6 and rnd.random() < 0.5:
        cuit_receptor = "20" + "".join(str(rnd.randint(0, 9)) for _ in range(9))
        cliente = rnd.choice(_RND_CLIENTES)
    else:
        cuit_receptor = ""
        cliente = "Consumidor Final"

    nro = rnd.randint(1, 99999)
    detail = {
        "invoice_number": f"INV-{rnd.randint(1000, 999999):06d}",
        "date": "2026-07-01",
        "due_date": "2026-07-31",
        "customer_name": cliente,
        "total": total,
        "tax_total": iva,
        "notes": rnd.choice(_RND_NOTAS),
        "line_items": line_items,
        "custom_fields": [],
    }
    arca_data = {
        "tipo_cbte": tipo,
        "punto_venta": rnd.choice([1, 1, 3]),
        "fecha": "2026-07-01",
        "fecha_vto": "2026-07-31",
        "cuit_receptor": cuit_receptor,
        "importe_neto": neto,
        "importe_iva": iva,
        "importe_total": total,
        "concepto": rnd.choice([1, 2, 3]),
        "alicuotas": alicuotas,
    }
    result = {
        "cae": rnd.randint(70_000_000_000_000, 79_999_999_999_999),
        "cae_vto": "20260711",
        "nro_comprobante": nro,
    }
    return detail, arca_data, result


_BUILDERS = {
    "b_servicios": _b_servicios,
    "a_productos": _a_productos,
    "c_monotributo": _c_monotributo,
    "b_larga": _b_larga,
    "aleatoria": _aleatoria,
}

# Samples por CONTENIDO (lo que ve el usuario en el selector de la preview):
# el tipo de comprobante se decide afuera (por la condición IVA del emisor).
# Cada entrada: (builder del contenido, tipo nativo estilo RI).
CONTENT_KINDS = ("servicios", "productos", "larga")

_CONTENT_BUILDERS = {
    "servicios": (_b_servicios, 6),
    "productos": (_a_productos, 1),
    "larga":    (_b_larga, 6),
}


def _retip_c(detail: dict, arca_data: dict) -> None:
    """Convierte un sample a Factura C (monotributo/exento): el total pasa a ser
    el neto, sin discriminación de IVA, y el receptor queda Consumidor Final."""
    neto = round(sum(i["item_total"] for i in detail["line_items"]), 2)
    arca_data["tipo_cbte"] = 11
    arca_data["cuit_receptor"] = ""
    arca_data["alicuotas"] = []
    arca_data["importe_iva"] = 0.0
    arca_data["importe_neto"] = neto
    arca_data["importe_total"] = neto
    detail["total"] = neto
    detail["tax_total"] = 0.0
    detail["customer_name"] = "Consumidor Final"


def sample_invoice(kind: str, tipo: int | None = None, tipos: list[int] | None = None):
    """Return ``(detail, arca_data, result, cuit_emisor)`` for a sample kind.

    - Content kinds (``servicios`` | ``productos`` | ``larga``): ``tipo`` elige
      el comprobante (el nativo estilo RI por defecto; 11 renderiza Factura C).
    - ``tipos`` solo afecta a ``kind="aleatoria"``: acota el sorteo.
    """
    if kind in _CONTENT_BUILDERS:
        builder, native = _CONTENT_BUILDERS[kind]
        detail, arca_data, result = builder()
        tipo = tipo if tipo is not None else native
        if tipo == native:
            pass
        elif tipo == 11:
            _retip_c(detail, arca_data)
        else:
            raise ValueError(f"tipo {tipo} no soportado para sample {kind!r}")
        return detail, arca_data, result, CUIT_EMISOR

    try:
        builder = _BUILDERS[kind]
    except KeyError:
        raise ValueError(
            f"Sample desconocido: {kind!r} "
            f"(válidos: {', '.join(SAMPLE_KINDS + CONTENT_KINDS)})"
        ) from None
    if kind == "aleatoria":
        detail, arca_data, result = builder(tipos)
    else:
        detail, arca_data, result = builder()
    return detail, arca_data, result, CUIT_EMISOR
