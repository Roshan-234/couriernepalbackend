"""The two documents the office actually issues, in the office's own layout.

Both are modelled on the workbooks the company has used for years rather
than on a generic template, because these go to customs officers, airline
agents and consignees who have seen this layout for two decades. A tidier
document that does not look like the company's own is a worse document.

Two things in those files surprised me and are preserved deliberately.

**A packing list carries the exporter's letterhead, not ours.** It is the
shipper's declaration of what is in their cartons — "EXAMPLE EXPORTS", with
their own P.O. box, telephone and email. Moonlight does not appear on it.
The previous generator printed "MOONLIGHT FREIGHT PVT. LTD. — PACKING LIST"
across the top, which is the wrong company on somebody else's declaration.

**Items are grouped into boxes.** The real lists read BOX NO:01, its
contents, BOX NO:02, its contents, and then one total across all of them.
Customs and the receiving agent both work carton by carton; a flat list of
items is the wrong shape for the people who use it.

The freight bill is likewise not a commercial invoice. It charges for
carriage, totals in US dollars, converts at the rate agreed that day and
settles in rupees, with the amount repeated in words — which is how a
Nepali business document is expected to close.
"""

from datetime import datetime

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, Side

from app.utils.excel import _safe, _sanitize

LEFT = Alignment(horizontal="left", vertical="center", wrap_text=False)
LEFT_WRAP = Alignment(horizontal="left", vertical="top", wrap_text=True)
CENTRE = Alignment(horizontal="center", vertical="center")
RIGHT = Alignment(horizontal="right", vertical="center")

#: The documents are typed, not designed. Courier New keeps them looking
#: like the originals rather than like a spreadsheet.
BODY = Font(name="Courier New", size=10)
BOLD = Font(name="Courier New", size=10, bold=True)
TITLE = Font(name="Courier New", size=12, bold=True)
COMPANY = Font(name="Courier New", size=12, bold=True)

UNDERLINE = Border(bottom=Side(style="thin"))


def _put(ws, row, col, value="", font=None, align=None, border=None):
    cell = ws.cell(row=row, column=col)
    cell.value = _safe(value)
    cell.font = font or BODY
    cell.alignment = align or LEFT
    if border:
        cell.border = border
    return cell


def _date(value):
    """dd-mm-yyyy, the format every one of these documents uses."""
    if not value:
        value = datetime.utcnow()
    return value.strftime("%d-%m-%Y")


# ── Amounts in words ─────────────────────────────────────────────────────────

_ONES = ("", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight",
         "Nine", "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen",
         "Sixteen", "Seventeen", "Eighteen", "Nineteen")
_TENS = ("", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy",
         "Eighty", "Ninety")


def _under_thousand(number):
    if number < 20:
        return _ONES[number]
    if number < 100:
        tens, ones = divmod(number, 10)
        return _TENS[tens] + (f" {_ONES[ones]}" if ones else "")
    hundreds, rest = divmod(number, 100)
    text = f"{_ONES[hundreds]} Hundred"
    return text + (f" {_under_thousand(rest)}" if rest else "")


def amount_in_words(amount, currency="Npr"):
    """"Twelve thousand two hundred forty five only."

    International scale, not lakh and crore: that is what the company's own
    bills use, and the figure has to read the same way to the consignee
    abroad as it does in the office.

    Paise are dropped rather than rounded up, matching the sample bills,
    where the words always restate the whole-rupee total.
    """
    # A missing amount is not zero. An unpriced bill should close with
    # nothing rather than assert in words that nothing is owed.
    if amount is None or amount == "":
        return ""
    try:
        whole = int(abs(float(amount)))
    except (TypeError, ValueError):
        return ""
    if whole == 0:
        return "Zero only."

    groups = (("Billion", 1_000_000_000), ("Million", 1_000_000),
              ("Thousand", 1_000))
    parts = []
    for name, size in groups:
        count, whole = divmod(whole, size)
        if count:
            parts.append(f"{_under_thousand(count)} {name}")
    if whole:
        parts.append(_under_thousand(whole))

    words = " ".join(parts)
    # The originals read "Twelve thousand two hundred forty five only" --
    # sentence case, not title case, after the first word.
    lowered = words[:1].upper() + words[1:].lower()
    return f"{lowered} only."


# ── Packing list ─────────────────────────────────────────────────────────────

def _boxes(items):
    """Group items under their box label, preserving the entered order.

    An item with no box falls into a single unlabelled group, so a simple
    list still produces a sensible document.
    """
    groups, order = {}, []
    for item in items or []:
        label = (item.get("box") or "").strip()
        if label not in groups:
            groups[label] = []
            order.append(label)
    for item in items or []:
        groups[(item.get("box") or "").strip()].append(item)
    return [(label, groups[label]) for label in order]


def generate_packing_list_excel(pl, storage_dir):
    """Write the packing list in the company's own layout.

    Returns ``(absolute_path, file_name)``.
    """
    import os

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "PACKING-LIST"
    sheet.page_setup.paperSize = sheet.PAPERSIZE_A4
    for column, width in zip("ABCDE", (8, 46, 12, 10, 18)):
        sheet.column_dimensions[column].width = width

    # Exporter letterhead. Theirs, not ours -- see the module docstring.
    _put(sheet, 1, 1, pl.sender_name, font=COMPANY)
    _put(sheet, 2, 1, pl.sender_address or "")
    if pl.sender_phone:
        _put(sheet, 3, 1, f"TEL:-{pl.sender_phone}")
    if getattr(pl, "sender_email", None):
        _put(sheet, 4, 1, f"Email:{pl.sender_email}")

    _put(sheet, 6, 1, "SHIPPED TO:", font=BOLD)
    if pl.invoice_no:
        _put(sheet, 6, 3, f"INV NO:-{pl.invoice_no}")
    _put(sheet, 7, 2, pl.receiver_name, font=BOLD)
    _put(sheet, 7, 3, f"DATE:-{_date(pl.created_at)}")
    _put(sheet, 8, 3, f"Mode of Shipment:-{pl.mode_of_shipment or 'BY-AIR'}")

    # The consignee address runs down column B, one line per line entered.
    row = 8
    for line in (pl.receiver_address or "").splitlines():
        if line.strip():
            _put(sheet, row, 2, line.strip())
            row += 1
    if pl.receiver_country:
        _put(sheet, row, 2, pl.receiver_country)
        row += 1
    if pl.receiver_phone:
        _put(sheet, row, 2, f"TEL:{pl.receiver_phone}")
        row += 1

    title_row = max(row + 1, 13)
    _put(sheet, title_row, 1, "PACKING-LIST", font=TITLE)

    head = title_row + 2
    for column, label in enumerate(("S.NO:", "DESCRIPTION", "CMS", "QTY",
                                    "REMARKS"), start=1):
        _put(sheet, head, column, label, font=BOLD, border=UNDERLINE)

    row = head + 1
    if pl.goods_summary:
        _put(sheet, row, 2, pl.goods_summary, font=BOLD)
        _put(sheet, row, 4, pl.quantity_unit or "PCS", font=BOLD, align=CENTRE)
        row += 1

    serial, total_quantity = 0, 0
    for label, items in _boxes(pl.items):
        if label:
            _put(sheet, row, 2, label, font=BOLD)
            row += 1
        for item in items:
            serial += 1
            quantity = item.get("quantity") or 0
            _put(sheet, row, 1, serial, align=CENTRE)
            _put(sheet, row, 2, item.get("description") or "")
            _put(sheet, row, 3, item.get("cms") or "", align=CENTRE)
            _put(sheet, row, 4, quantity, align=CENTRE)
            _put(sheet, row, 5, item.get("remarks") or "")
            try:
                total_quantity += int(quantity)
            except (TypeError, ValueError):
                pass
            row += 1

    _put(sheet, row, 2, "TOTAL", font=BOLD, border=UNDERLINE)
    _put(sheet, row, 4, total_quantity, font=BOLD, align=CENTRE, border=UNDERLINE)

    _put(sheet, row + 6, 3, f"For: {pl.signed_for or pl.sender_name}.")

    name = f"PACKING-LIST-{_sanitize(pl.pl_number)}.xlsx"
    path = os.path.abspath(os.path.join(storage_dir, name))
    workbook.save(path)
    return path, name


# ── Freight bill ─────────────────────────────────────────────────────────────

def generate_invoice_excel(inv, storage_dir):
    """Write the freight bill in the company's own layout.

    Returns ``(absolute_path, file_name)``.
    """
    import os

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "INVOICE BILL"
    sheet.page_setup.paperSize = sheet.PAPERSIZE_A4
    for column, width in zip("ABCDEFG", (6, 46, 14, 7, 7, 7, 22)):
        sheet.column_dimensions[column].width = width

    _put(sheet, 1, 1, inv.seller_name, font=COMPANY)
    _put(sheet, 2, 1, inv.seller_country or "KATHMANDU,NEPAL")
    if inv.seller_phone:
        _put(sheet, 3, 1, f"TEL:{inv.seller_phone}")
    if getattr(inv, "seller_email", None):
        _put(sheet, 4, 1, f"EMAIL:{inv.seller_email}")

    _put(sheet, 5, 2, f"BILL NO:{inv.invoice_number}", font=BOLD)

    issued = inv.created_at or datetime.utcnow()
    _put(sheet, 6, 2, f"Date:{_date(issued)}")
    for column, label in ((3, "AWB NO."), (4, "DAY"), (5, "MON."),
                          (6, "YR."), (7, "Account")):
        _put(sheet, 6, column, label, font=BOLD, align=CENTRE, border=UNDERLINE)

    _put(sheet, 7, 1, "TO", font=BOLD)
    _put(sheet, 7, 2, inv.buyer_name, font=BOLD)
    _put(sheet, 7, 3, inv.awb_no or "", align=CENTRE)
    # Day, month and year of the waybill in their own columns, which is how
    # the office files and finds these.
    _put(sheet, 7, 4, issued.day, align=CENTRE)
    _put(sheet, 7, 5, issued.month, align=CENTRE)
    _put(sheet, 7, 6, issued.year, align=CENTRE)
    _put(sheet, 7, 7, inv.account_name or "")

    row = 8
    for line in (inv.buyer_address or "").splitlines():
        if line.strip():
            _put(sheet, row, 2, line.strip())
            row += 1
    if inv.buyer_country:
        _put(sheet, row, 2, inv.buyer_country)
        row += 1

    particulars = max(row + 1, 10)
    _put(sheet, particulars, 2, "PARTICULAR", font=BOLD, border=UNDERLINE)

    row = particulars + 1
    subtotal = 0.0
    for item in inv.items or []:
        description = item.get("description") or ""
        amount = item.get("total_price", item.get("amount"))
        if amount in (None, ""):
            _put(sheet, row, 2, description)
        else:
            try:
                value = float(amount)
                subtotal += value
                _put(sheet, row, 2, f"{description} = {inv.currency or 'USD'} {value:,.2f}")
            except (TypeError, ValueError):
                _put(sheet, row, 2, description)
        row += 1

    total_npr = inv.total_npr
    if inv.fx_rate:
        _put(sheet, row, 2,
             f"Total {(inv.currency or 'USD').lower()} ={subtotal:,.2f}@{inv.fx_rate}")
        if total_npr in (None, 0):
            total_npr = round(subtotal * float(inv.fx_rate), 2)
        row += 1

    _put(sheet, row + 1, 2, "Total", font=BOLD, border=UNDERLINE)
    _put(sheet, row + 1, 3, "Npr", font=BOLD, border=UNDERLINE, align=CENTRE)
    _put(sheet, row + 1, 4, round(total_npr or 0, 2), font=BOLD, border=UNDERLINE,
         align=RIGHT)

    words = inv.amount_in_words or amount_in_words(total_npr)
    _put(sheet, row + 8, 2, f"In words:{words}")

    name = f"INVOICE-{_sanitize(inv.invoice_number)}.xlsx"
    path = os.path.abspath(os.path.join(storage_dir, name))
    workbook.save(path)
    return path, name
