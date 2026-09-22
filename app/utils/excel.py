"""
Excel generation utilities for Packing Lists and Commercial Invoices.
Uses openpyxl with professional formatting — company branded header,
proper borders, column widths, and print layout.
"""
import os
import re
from datetime import datetime
from openpyxl import Workbook
from openpyxl.styles import (
    Font, PatternFill, Alignment, Border, Side, numbers
)
from openpyxl.utils import get_column_letter

# ── Brand colours ────────────────────────────────────────────────────────────
BRAND_BLUE   = "0C3E6F"   # dark blue header
BRAND_ORANGE = "F27318"   # accent
LIGHT_GRAY   = "F3F4F6"   # alternate rows
MID_GRAY     = "E5E7EB"
WHITE        = "FFFFFF"
DARK         = "1F2937"

def _thin_border():
    side = Side(style='thin', color="D1D5DB")
    return Border(left=side, right=side, top=side, bottom=side)

def _thick_border():
    side = Side(style='medium', color=BRAND_BLUE)
    return Border(left=side, right=side, top=side, bottom=side)

def _header_fill():  return PatternFill("solid", fgColor=BRAND_BLUE)
def _accent_fill():  return PatternFill("solid", fgColor=BRAND_ORANGE)
def _gray_fill():    return PatternFill("solid", fgColor=LIGHT_GRAY)
def _white_fill():   return PatternFill("solid", fgColor=WHITE)

def _header_font(size=11):  return Font(name="Calibri", bold=True, color=WHITE,  size=size)
def _bold_font(size=10):    return Font(name="Calibri", bold=True, color=DARK,   size=size)
def _normal_font(size=10):  return Font(name="Calibri", bold=False, color=DARK,  size=size)

def _set_col_width(ws, col, width):
    ws.column_dimensions[get_column_letter(col)].width = width

def _merge(ws, r1, c1, r2, c2):
    ws.merge_cells(start_row=r1, start_column=c1, end_row=r2, end_column=c2)

def _cell(ws, row, col, value="", font=None, fill=None, align=None, border=None, number_format=None):
    c = ws.cell(row=row, column=col, value=value)
    if font:          c.font          = font
    if fill:          c.fill          = fill
    if align:         c.alignment     = align
    if border:        c.border        = border
    if number_format: c.number_format = number_format
    return c

CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)
LEFT   = Alignment(horizontal="left",   vertical="center", wrap_text=True)
RIGHT  = Alignment(horizontal="right",  vertical="center")

def _sanitize(name: str) -> str:
    """Make a safe filename component from a party name."""
    return re.sub(r'[^\w\-]', '_', name.strip())[:40]

# ─────────────────────────────────────────────────────────────────────────────
# PACKING LIST
# ─────────────────────────────────────────────────────────────────────────────

def generate_packing_list_excel(pl, storage_dir: str) -> tuple[str, str]:
    """
    Generate a packing-list Excel file.
    Returns (absolute_file_path, file_name).
    """
    wb = Workbook()
    ws = wb.active
    ws.title = "Packing List"
    ws.page_setup.orientation = "landscape"
    ws.page_setup.paperSize   = ws.PAPERSIZE_A4
    ws.page_margins.left = ws.page_margins.right = 0.5
    ws.print_title_rows = "1:1"

    # Column widths (A-J)
    widths = [5, 28, 10, 8, 12, 14, 18, 14, 5, 0]
    for i, w in enumerate(widths, 1):
        if w: _set_col_width(ws, i, w)

    # ── Row 1: Company header ────────────────────────────────────────────────
    ws.row_dimensions[1].height = 36
    _merge(ws, 1, 1, 1, 9)
    _cell(ws, 1, 1,
          "MOONLIGHT FREIGHT PVT. LTD. — PACKING LIST",
          font=Font(name="Calibri", bold=True, color=WHITE, size=16),
          fill=_header_fill(),
          align=CENTER)

    # ── Row 2: Document number & date ───────────────────────────────────────
    ws.row_dimensions[2].height = 22
    _merge(ws, 2, 1, 2, 5)
    _cell(ws, 2, 1, f"PL No: {pl.pl_number}",
          font=_bold_font(11), fill=PatternFill("solid", fgColor="1E3A5F"),
          align=LEFT)
    _merge(ws, 2, 6, 2, 9)
    _cell(ws, 2, 6, f"Date: {pl.created_at.strftime('%d %B %Y')}",
          font=_bold_font(11), fill=PatternFill("solid", fgColor="1E3A5F"),
          align=RIGHT)
    for col in range(1, 10):
        ws.cell(2, col).font = Font(name="Calibri", bold=True, color=WHITE, size=11)

    # ── Rows 3-6: Shipper / Consignee ───────────────────────────────────────
    ws.row_dimensions[3].height = 16
    _merge(ws, 3, 1, 3, 4)
    _cell(ws, 3, 1, "SHIPPER / EXPORTER", font=_bold_font(), fill=_gray_fill(), align=LEFT)
    _merge(ws, 3, 5, 3, 9)
    _cell(ws, 3, 5, "CONSIGNEE / IMPORTER", font=_bold_font(), fill=_gray_fill(), align=LEFT)

    shipper_lines = [
        pl.sender_name,
        pl.sender_address or "",
        pl.sender_phone or "",
        pl.sender_country or "Nepal",
    ]
    consignee_lines = [
        pl.receiver_name,
        pl.receiver_address or "",
        pl.receiver_phone or "",
        pl.receiver_country or "",
    ]
    for i, (s, c) in enumerate(zip(shipper_lines, consignee_lines)):
        r = 4 + i
        ws.row_dimensions[r].height = 15
        _merge(ws, r, 1, r, 4)
        _cell(ws, r, 1, s, font=_normal_font(), align=LEFT)
        _merge(ws, r, 5, r, 9)
        _cell(ws, r, 5, c, font=_normal_font(), align=LEFT)

    if pl.shipment_id:
        ws.row_dimensions[8].height = 15
        _merge(ws, 8, 1, 8, 9)
        from app.models.shipment import Shipment
        s = Shipment.query.get(pl.shipment_id)
        tn = s.tracking_no if s else ""
        _cell(ws, 8, 1, f"Tracking No: {tn}",
              font=Font(name="Calibri", bold=True, color=BRAND_ORANGE, size=10), align=LEFT)

    # ── Row 9: Table header ─────────────────────────────────────────────────
    ws.row_dimensions[9].height = 20
    headers = ["SL#", "Description of Goods", "Qty", "Unit", "Unit Wt(kg)", "Total Wt(kg)", "Country of Origin", "HS Code"]
    for col, h in enumerate(headers, 1):
        _cell(ws, 9, col, h,
              font=_header_font(10),
              fill=PatternFill("solid", fgColor=BRAND_ORANGE),
              align=CENTER,
              border=_thin_border())

    # ── Item rows ────────────────────────────────────────────────────────────
    start_row = 10
    total_qty = 0
    total_wt  = 0.0
    items = pl.items or []

    for idx, item in enumerate(items):
        r   = start_row + idx
        fill= _white_fill() if idx % 2 == 0 else _gray_fill()
        ws.row_dimensions[r].height = 16

        _cell(ws, r, 1, idx + 1,         font=_normal_font(), fill=fill, align=CENTER, border=_thin_border())
        _cell(ws, r, 2, item.get("description",""), font=_normal_font(), fill=fill, align=LEFT,   border=_thin_border())
        _cell(ws, r, 3, item.get("quantity", 0),    font=_normal_font(), fill=fill, align=CENTER, border=_thin_border())
        _cell(ws, r, 4, item.get("unit", "pcs"),    font=_normal_font(), fill=fill, align=CENTER, border=_thin_border())
        _cell(ws, r, 5, item.get("unit_weight_kg",0), font=_normal_font(), fill=fill, align=RIGHT, border=_thin_border(), number_format="0.00")
        tw = item.get("total_weight_kg") or (item.get("quantity",0) * item.get("unit_weight_kg",0))
        _cell(ws, r, 6, tw,              font=_normal_font(), fill=fill, align=RIGHT, border=_thin_border(), number_format="0.00")
        _cell(ws, r, 7, item.get("country_of_origin","Nepal"), font=_normal_font(), fill=fill, align=CENTER, border=_thin_border())
        _cell(ws, r, 8, item.get("hs_code",""),     font=_normal_font(), fill=fill, align=CENTER, border=_thin_border())

        total_qty += int(item.get("quantity", 0))
        total_wt  += float(tw)

    # ── Totals row ───────────────────────────────────────────────────────────
    tr = start_row + len(items)
    ws.row_dimensions[tr].height = 18
    _merge(ws, tr, 1, tr, 2)
    _cell(ws, tr, 1, "TOTAL", font=_bold_font(), fill=_gray_fill(), align=CENTER, border=_thin_border())
    _cell(ws, tr, 3, total_qty, font=_bold_font(), fill=_gray_fill(), align=CENTER, border=_thin_border())
    _cell(ws, tr, 4, "", fill=_gray_fill(), border=_thin_border())
    _cell(ws, tr, 5, "", fill=_gray_fill(), border=_thin_border())
    _cell(ws, tr, 6, round(total_wt,2), font=_bold_font(), fill=_gray_fill(), align=RIGHT,
          border=_thin_border(), number_format="0.00")
    for c in [7, 8]: _cell(ws, tr, c, "", fill=_gray_fill(), border=_thin_border())

    # ── Notes ────────────────────────────────────────────────────────────────
    if pl.notes:
        nr = tr + 1
        ws.row_dimensions[nr].height = 30
        _merge(ws, nr, 1, nr, 9)
        _cell(ws, nr, 1, f"Notes: {pl.notes}", font=_normal_font(), align=LEFT)

    # ── Footer / signature ───────────────────────────────────────────────────
    fr = tr + (3 if pl.notes else 2)
    _merge(ws, fr, 1, fr, 4)
    _cell(ws, fr, 1, "Authorised Signature & Stamp", font=_bold_font(), align=CENTER)
    _merge(ws, fr, 5, fr, 9)
    _cell(ws, fr, 5, "Receiver's Signature", font=_bold_font(), align=CENTER)

    # ── Save ─────────────────────────────────────────────────────────────────
    safe_sender   = _sanitize(pl.sender_name)
    safe_receiver = _sanitize(pl.receiver_name)
    fname = f"PL_{pl.pl_number}_{safe_sender}_to_{safe_receiver}_{datetime.utcnow().strftime('%Y%m%d')}.xlsx"
    fpath = os.path.join(storage_dir, "packing_lists", fname)
    os.makedirs(os.path.dirname(fpath), exist_ok=True)
    wb.save(fpath)
    return fpath, fname


# ─────────────────────────────────────────────────────────────────────────────
# COMMERCIAL INVOICE
# ─────────────────────────────────────────────────────────────────────────────

def generate_invoice_excel(inv, storage_dir: str) -> tuple[str, str]:
    """
    Generate a commercial invoice Excel file.
    Returns (absolute_file_path, file_name).
    """
    wb = Workbook()
    ws = wb.active
    ws.title = "Commercial Invoice"
    ws.page_setup.orientation = "portrait"
    ws.page_setup.paperSize   = ws.PAPERSIZE_A4
    ws.page_margins.left = ws.page_margins.right = 0.6

    # Column widths (A-H)
    for i, w in enumerate([5, 30, 8, 8, 12, 12, 12, 12], 1):
        _set_col_width(ws, i, w)

    # ── Header ───────────────────────────────────────────────────────────────
    ws.row_dimensions[1].height = 40
    _merge(ws, 1, 1, 1, 8)
    _cell(ws, 1, 1, "COMMERCIAL INVOICE",
          font=Font(name="Calibri", bold=True, color=WHITE, size=18),
          fill=_header_fill(), align=CENTER)

    ws.row_dimensions[2].height = 14
    _merge(ws, 2, 1, 2, 8)
    _cell(ws, 2, 1, "Moonlight Freight Pvt. Ltd. | Nayabazar, Kathmandu, Nepal | +977-1-5922458",
          font=Font(name="Calibri", color="BFDBFE", size=9), align=CENTER,
          fill=PatternFill("solid", fgColor="1E3A5F"))

    # ── Invoice meta ─────────────────────────────────────────────────────────
    ws.row_dimensions[3].height = 5

    def meta_row(r, label, value, label_col=1, value_col=2, end_col=4):
        ws.row_dimensions[r].height = 16
        _cell(ws, r, label_col, label, font=_bold_font(9), align=LEFT)
        _merge(ws, r, value_col, r, end_col)
        _cell(ws, r, value_col, value, font=_normal_font(9), align=LEFT)

    meta_row(4, "Invoice No:",   inv.invoice_number)
    meta_row(5, "Date:",         inv.created_at.strftime("%d %B %Y"))
    meta_row(6, "Due Date:",     inv.due_date.strftime("%d %B %Y") if inv.due_date else "")
    meta_row(7, "Payment Terms:",inv.payment_terms or "")
    meta_row(8, "Incoterms:",    inv.incoterms or "")
    if inv.shipment_id:
        from app.models.shipment import Shipment
        s = Shipment.query.get(inv.shipment_id)
        meta_row(9, "Tracking No:", s.tracking_no if s else "")
    meta_row(10,"Currency:",     inv.currency)

    # right side — port info
    meta_row(4, "Port of Loading:",   inv.port_of_loading  or "", 5, 6, 8)
    meta_row(5, "Port of Discharge:", inv.port_of_discharge or "", 5, 6, 8)

    # ── Seller / Buyer ───────────────────────────────────────────────────────
    ws.row_dimensions[11].height = 5
    ws.row_dimensions[12].height = 16
    _merge(ws, 12, 1, 12, 4)
    _cell(ws, 12, 1, "SELLER / EXPORTER", font=_bold_font(), fill=_gray_fill(), align=LEFT)
    _merge(ws, 12, 5, 12, 8)
    _cell(ws, 12, 5, "BUYER / IMPORTER",  font=_bold_font(), fill=_gray_fill(), align=LEFT)

    seller_lines = [inv.seller_name, inv.seller_address or "", inv.seller_phone or "", inv.seller_country or "Nepal", f"TIN: {inv.seller_tin}" if inv.seller_tin else ""]
    buyer_lines  = [inv.buyer_name,  inv.buyer_address  or "", inv.buyer_phone  or "", inv.buyer_country  or "",      f"TIN: {inv.buyer_tin}"  if inv.buyer_tin  else ""]
    for i, (s, b) in enumerate(zip(seller_lines, buyer_lines)):
        r = 13 + i
        ws.row_dimensions[r].height = 14
        _merge(ws, r, 1, r, 4); _cell(ws, r, 1, s, font=_normal_font(9), align=LEFT)
        _merge(ws, r, 5, r, 8); _cell(ws, r, 5, b, font=_normal_font(9), align=LEFT)

    # ── Items table ──────────────────────────────────────────────────────────
    ws.row_dimensions[18].height = 5
    ws.row_dimensions[19].height = 20
    item_headers = ["SL#", "Description of Goods", "Qty", "Unit", "Unit Price", "Amount", "HS Code", "C.O.O"]
    for col, h in enumerate(item_headers, 1):
        _cell(ws, 19, col, h,
              font=_header_font(10),
              fill=PatternFill("solid", fgColor=BRAND_ORANGE),
              align=CENTER, border=_thin_border())

    items      = inv.items or []
    start_row  = 20
    total_qty  = 0
    for idx, item in enumerate(items):
        r    = start_row + idx
        fill = _white_fill() if idx % 2 == 0 else _gray_fill()
        ws.row_dimensions[r].height = 15

        amt = item.get("total_price") or (item.get("quantity", 0) * item.get("unit_price", 0))
        _cell(ws, r, 1, idx+1,                   font=_normal_font(), fill=fill, align=CENTER, border=_thin_border())
        _cell(ws, r, 2, item.get("description",""),font=_normal_font(), fill=fill, align=LEFT,   border=_thin_border())
        _cell(ws, r, 3, item.get("quantity",0),  font=_normal_font(), fill=fill, align=CENTER, border=_thin_border())
        _cell(ws, r, 4, item.get("unit","pcs"),  font=_normal_font(), fill=fill, align=CENTER, border=_thin_border())
        _cell(ws, r, 5, item.get("unit_price",0),font=_normal_font(), fill=fill, align=RIGHT,  border=_thin_border(), number_format="#,##0.00")
        _cell(ws, r, 6, amt,                     font=_normal_font(), fill=fill, align=RIGHT,  border=_thin_border(), number_format="#,##0.00")
        _cell(ws, r, 7, item.get("hs_code",""),  font=_normal_font(), fill=fill, align=CENTER, border=_thin_border())
        _cell(ws, r, 8, item.get("country_of_origin","Nepal"), font=_normal_font(), fill=fill, align=CENTER, border=_thin_border())
        total_qty += int(item.get("quantity", 0))

    # ── Subtotals ─────────────────────────────────────────────────────────────
    tr = start_row + len(items)
    def summary_row(r, label, value, bold=False):
        ws.row_dimensions[r].height = 15
        _merge(ws, r, 1, r, 5)
        _cell(ws, r, 1, label,
              font=_bold_font() if bold else _normal_font(),
              fill=_gray_fill() if bold else _white_fill(), align=RIGHT)
        _merge(ws, r, 6, r, 8)
        _cell(ws, r, 6, value,
              font=_bold_font() if bold else _normal_font(),
              fill=_gray_fill() if bold else _white_fill(),
              align=RIGHT, number_format="#,##0.00")

    summary_row(tr,   f"Subtotal ({inv.currency})",          inv.subtotal)
    summary_row(tr+1, f"Discount",                           -inv.discount if inv.discount else 0)
    summary_row(tr+2, f"Tax ({inv.tax_percentage}%)",        inv.tax_amount)
    summary_row(tr+3, f"Shipping Charge",                    inv.shipping_charge)
    summary_row(tr+4, f"TOTAL ({inv.currency})",             inv.total_amount, bold=True)

    # ── Notes ─────────────────────────────────────────────────────────────────
    if inv.notes:
        nr = tr + 5
        ws.row_dimensions[nr].height = 30
        _merge(ws, nr, 1, nr, 8)
        _cell(ws, nr, 1, f"Notes: {inv.notes}", font=_normal_font(9), align=LEFT)

    # ── Signature ─────────────────────────────────────────────────────────────
    sr = tr + (7 if inv.notes else 6)
    _merge(ws, sr, 1, sr, 4)
    _cell(ws, sr, 1, "Authorised Signature & Stamp", font=_bold_font(), align=CENTER)
    _merge(ws, sr, 5, sr, 8)
    _cell(ws, sr, 5, "For & On Behalf of Importer", font=_bold_font(), align=CENTER)

    # ── Save ─────────────────────────────────────────────────────────────────
    safe_seller = _sanitize(inv.seller_name)
    safe_buyer  = _sanitize(inv.buyer_name)
    fname = f"INV_{inv.invoice_number}_{safe_seller}_to_{safe_buyer}_{datetime.utcnow().strftime('%Y%m%d')}.xlsx"
    fpath = os.path.join(storage_dir, "invoices", fname)
    os.makedirs(os.path.dirname(fpath), exist_ok=True)
    wb.save(fpath)
    return fpath, fname
