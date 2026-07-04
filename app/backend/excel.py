"""Build a formula-driven .xlsx (no macros) detailing the model-price calc.

Each bond gets a sheet whose cells reconstruct the reduced-form dirty price with
live Excel formulas: discount factors interpolated from the IR-curve sheet and
survival probabilities from the credit-curve sheet (log-linear on the integrated
rate/hazard, matching cdslib), then coupon and recovery PVs summed. Opening the
file lets an analyst audit every number.
"""

from __future__ import annotations

import io
from datetime import date, timedelta
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from app.backend.credit_service import CreditContext
from cdslib import Bond as QuantBond
from cdslib import BondPriceFit

_HEADER = Font(bold=True)
_ACCENT = Font(bold=True, color="0B5D8A")
_MUTED = Font(color="64748B")
_HEAD_FILL = PatternFill("solid", fgColor="E8EEF5")
_DATE_FMT = "yyyy-mm-dd"
_PX_FMT = "0.0000"
_FACTOR_FMT = "0.00000000"


def build_workbook(ctx: CreditContext) -> bytes:
    wb = Workbook()
    summary = wb.active
    summary.title = "Summary"

    ir_ref = _write_ir_curve(wb.create_sheet("IR Curve"), ctx)
    cr_ref = _write_credit_curve(wb.create_sheet("Credit Curve"), ctx)

    bond_sheets: list[tuple[str, int]] = []  # (sheet name, model-dirty cell row)
    for info, fit, quant in zip(ctx.meta, ctx.result.fits, ctx.quant_bonds, strict=True):
        name = _write_bond_sheet(wb, ctx, info, fit, quant, ir_ref, cr_ref)
        bond_sheets.append(name)

    _write_summary(summary, ctx, bond_sheets)
    stream = io.BytesIO()
    wb.save(stream)
    return stream.getvalue()


# --- curve sheets ---------------------------------------------------------


class _CurveRef:
    def __init__(self, sheet: str, first: int, last: int) -> None:
        q = f"'{sheet}'"
        self.days = f"{q}!$B${first}:$B${last}"
        self.integrated = f"{q}!$D${first}:$D${last}"
        self.last_day = f"{q}!$B${last}"
        self.n = last - first + 1


def _write_ir_curve(ws: Worksheet, ctx: CreditContext) -> _CurveRef:
    ws["A1"] = "Interest-rate (discount) curve"
    ws["A1"].font = _ACCENT
    headers = ["Date", "Days", "Zero rate %", "Integrated r*t"]
    for col, text in enumerate(headers, start=1):
        cell = ws.cell(2, col, text)
        cell.font = _HEADER
        cell.fill = _HEAD_FILL
    base = ctx.base
    rows = [(base, 0, 0.0)] + [
        (base + _days(p.tenor_days), p.tenor_days, p.zero_rate * 100.0)
        for p in ctx.discount.points()
    ]
    first = 3
    for i, (node_date, days, zero_pct) in enumerate(rows):
        r = first + i
        ws.cell(r, 1, node_date).number_format = _DATE_FMT
        ws.cell(r, 2, days)
        ws.cell(r, 3, zero_pct).number_format = _PX_FMT
        ws.cell(r, 4, f"=C{r}/100*B{r}/365").number_format = _FACTOR_FMT
    _autosize(ws, [12, 8, 12, 14])
    return _CurveRef("IR Curve", first, first + len(rows) - 1)


def _write_credit_curve(ws: Worksheet, ctx: CreditContext) -> _CurveRef:
    ws["A1"] = "Credit (survival / hazard) curve"
    ws["A1"].font = _ACCENT
    headers = ["Date", "Days", "Hazard %", "Integrated h*t", "Survival"]
    for col, text in enumerate(headers, start=1):
        cell = ws.cell(2, col, text)
        cell.font = _HEADER
        cell.fill = _HEAD_FILL
    base = ctx.base
    rows = [(base, 0, 0.0)] + [
        (base + _days(p.tenor_days), p.tenor_days, p.hazard * 100.0)
        for p in ctx.result.curve.points()
    ]
    first = 3
    for i, (node_date, days, hazard_pct) in enumerate(rows):
        r = first + i
        ws.cell(r, 1, node_date).number_format = _DATE_FMT
        ws.cell(r, 2, days)
        ws.cell(r, 3, hazard_pct).number_format = _PX_FMT
        ws.cell(r, 4, f"=C{r}/100*B{r}/365").number_format = _FACTOR_FMT
        ws.cell(r, 5, f"=EXP(-D{r})").number_format = _FACTOR_FMT
    _autosize(ws, [12, 8, 12, 14, 12])
    return _CurveRef("Credit Curve", first, first + len(rows) - 1)


# --- per-bond sheet -------------------------------------------------------


def _write_bond_sheet(
    wb: Workbook,
    ctx: CreditContext,
    info: dict[str, Any],
    fit: BondPriceFit,
    quant: QuantBond,
    ir: _CurveRef,
    cr: _CurveRef,
) -> tuple[str, int]:
    ws = wb.create_sheet(_safe_title(info["isin"]))
    ws["A1"] = "Model price detail"
    ws["A1"].font = _ACCENT

    facts = [
        ("ISIN", info["isin"]),
        ("Name", info["name"]),
        ("Face value", info["face"]),
        ("Recovery R", ctx.recovery),
        ("Accrued", info["accrued"]),
        ("Base (valuation) date", ctx.base),
    ]
    for i, (label, value) in enumerate(facts):
        r = 2 + i
        ws.cell(r, 1, label).font = _MUTED
        cell = ws.cell(r, 2, value)
        if isinstance(value, date):
            cell.number_format = _DATE_FMT
    # B3=face, B5=recovery, B6=accrued, B7=base
    face_ref, rec_ref, accr_ref, base_ref = "$B$4", "$B$5", "$B$6", "$B$7"

    head_row = 9
    headers = [
        "Date", "Days", "Coupon", "Principal", "Outstanding",
        "d_IR", "k_IR", "DF", "d_CR", "k_CR", "Q", "Q_prev",
        "Coupon PV", "Recovery PV", "Row PV",
    ]
    for col, text in enumerate(headers, start=1):
        cell = ws.cell(head_row, col, text)
        cell.font = _HEADER
        cell.fill = _HEAD_FILL

    flows = sorted(
        (cf for cf in quant.cashflows if cf.date > ctx.base), key=lambda cf: cf.date
    )
    first = head_row + 1
    for i, cf in enumerate(flows):
        r = first + i
        ws.cell(r, 1, cf.date).number_format = _DATE_FMT
        ws.cell(r, 2, f"=A{r}-{base_ref}")
        ws.cell(r, 3, cf.coupon).number_format = _PX_FMT
        ws.cell(r, 4, cf.principal).number_format = _PX_FMT
        ws.cell(
            r, 5, f"={face_ref}" if i == 0 else f"=E{r-1}-D{r-1}"
        ).number_format = _PX_FMT
        # discount factor via log-linear interpolation of integrated rate
        ws.cell(r, 6, f"=MIN(B{r},{ir.last_day})")
        ws.cell(r, 7, f"=MIN(MATCH(F{r},{ir.days},1),{ir.n - 1})")
        ws.cell(r, 8, _interp_exp("F", "G", r, ir)).number_format = _FACTOR_FMT
        # survival via log-linear interpolation of integrated hazard
        ws.cell(r, 9, f"=MIN(B{r},{cr.last_day})")
        ws.cell(r, 10, f"=MIN(MATCH(I{r},{cr.days},1),{cr.n - 1})")
        ws.cell(r, 11, _interp_exp("I", "J", r, cr)).number_format = _FACTOR_FMT
        ws.cell(r, 12, "=1" if i == 0 else f"=K{r-1}").number_format = _FACTOR_FMT
        ws.cell(r, 13, f"=(C{r}+D{r})*H{r}*K{r}").number_format = _PX_FMT
        ws.cell(r, 14, f"={rec_ref}*E{r}*H{r}*(L{r}-K{r})").number_format = _PX_FMT
        ws.cell(r, 15, f"=M{r}+N{r}").number_format = _PX_FMT
    last = first + len(flows) - 1

    tr = last + 2
    ws.cell(tr, 12, "Model dirty (formula)").font = _HEADER
    dirty_cell = f"O{tr}"
    ws[dirty_cell] = f"=SUM(O{first}:O{last})"
    ws[dirty_cell].number_format = _PX_FMT
    ws[dirty_cell].font = _HEADER
    ws.cell(tr + 1, 12, "Model clean (formula)")
    ws.cell(tr + 1, 15, f"=(O{tr}-{accr_ref})/{face_ref}*100").number_format = _PX_FMT
    ws.cell(tr + 2, 12, "Model dirty (app)").font = _MUTED
    ws.cell(tr + 2, 15, fit.model_dirty).number_format = _PX_FMT
    ws.cell(tr + 2, 15).font = _MUTED
    ws.cell(tr + 3, 12, "Market clean (used)").font = _MUTED
    ws.cell(tr + 3, 15, info["market_clean"]).number_format = _PX_FMT

    _autosize(ws, [12, 7, 9, 9, 11, 8, 6, 11, 8, 6, 11, 10, 11, 12, 11])
    return _safe_title(info["isin"]), tr


def _interp_exp(day_col: str, k_col: str, r: int, ref: _CurveRef) -> str:
    """EXP(-(linear interpolation of integrated value at capped day))."""
    d = f"{day_col}{r}"
    k = f"{k_col}{r}"
    x0 = f"INDEX({ref.days},{k})"
    x1 = f"INDEX({ref.days},{k}+1)"
    y0 = f"INDEX({ref.integrated},{k})"
    y1 = f"INDEX({ref.integrated},{k}+1)"
    return f"=EXP(-({y0}+({d}-{x0})*({y1}-{y0})/({x1}-{x0})))"


# --- summary --------------------------------------------------------------


def _write_summary(
    ws: Worksheet, ctx: CreditContext, bond_sheets: list[tuple[str, int]]
) -> None:
    ws["A1"] = "Credit-curve model price detail"
    ws["A1"].font = _ACCENT
    facts = [
        ("Issuer", ctx.issuer_name),
        ("Trade date", ctx.trade),
        ("Valuation (base) date", ctx.base),
        ("Recovery rate R", ctx.recovery),
    ]
    for i, (label, value) in enumerate(facts):
        r = 2 + i
        ws.cell(r, 1, label).font = _MUTED
        cell = ws.cell(r, 2, value)
        if isinstance(value, date):
            cell.number_format = _DATE_FMT

    head = 8
    for col, text in enumerate(
        ["Bond", "ISIN", "Market clean", "Model clean (app)", "Model dirty (formula)"],
        start=1,
    ):
        cell = ws.cell(head, col, text)
        cell.font = _HEADER
        cell.fill = _HEAD_FILL

    for i, (info, fit, (sheet, dirty_row)) in enumerate(
        zip(ctx.meta, ctx.result.fits, bond_sheets, strict=True)
    ):
        r = head + 1 + i
        ws.cell(r, 1, info["name"])
        ws.cell(r, 2, info["isin"])
        ws.cell(r, 3, info["market_clean"]).number_format = _PX_FMT
        model_clean = (fit.model_dirty - info["accrued"]) / (info["face"] or 1.0) * 100.0
        ws.cell(r, 4, model_clean).number_format = _PX_FMT
        ws.cell(r, 5, f"='{sheet}'!O{dirty_row}").number_format = _PX_FMT

    if ctx.skipped:
        r = head + 2 + len(bond_sheets)
        ws.cell(r, 1, "Excluded:").font = _MUTED
        ws.cell(r, 2, ", ".join(f"{s['isin']} ({s['reason']})" for s in ctx.skipped))
    _autosize(ws, [26, 15, 13, 16, 20])


# --- helpers --------------------------------------------------------------


def _days(n: int) -> timedelta:
    return timedelta(days=n)


def _safe_title(isin: str) -> str:
    bad = set('[]:*?/\\')
    return "".join(c for c in isin if c not in bad)[:31]


def _autosize(ws: Worksheet, widths: list[int]) -> None:
    for i, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = width
