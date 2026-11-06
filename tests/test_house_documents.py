"""The packing list and freight bill, in the company's own layout.

These two documents go to customs officers, airline agents and consignees
who have seen the same layout for twenty years. The tests below pin the
parts that are easy to "improve" into something wrong.

The layouts were taken from the company's own workbooks. Those files are
their live books — thousands of real customers, addresses and amounts —
so nothing from them is reproduced here or committed anywhere. Only the
shape was used.
"""

import os

import pytest
from openpyxl import load_workbook

from app.extensions import db
from app.models.invoice import Invoice
from app.models.packing_list import PackingList
from app.utils.house_format import (
    amount_in_words,
    generate_invoice_excel,
    generate_packing_list_excel,
)


@pytest.fixture
def output_dir(tmp_path):
    directory = tmp_path / "documents"
    directory.mkdir()
    return str(directory)


@pytest.fixture(scope="module")
def packing_list(app, admin_token):
    with app.app_context():
        from app.models.user import User
        existing = PackingList.query.filter_by(pl_number="PL-HOUSE-1").first()
        if existing:
            return existing.id
        creator = User.query.first()
        record = PackingList(
            pl_number="PL-HOUSE-1",
            created_by=creator.id,
            sender_name="EXAMPLE EXPORTS",
            sender_address="P.O.BOX NO:4223,THAMEL,KATHMANDU,NEPAL",
            sender_phone="977-1-0000000",
            sender_email="info@example.invalid",
            receiver_name="EXAMPLE CONSIGNEE SRL",
            receiver_address="C/O A PERSON\nSOME STREET",
            receiver_country="BELGIUM",
            receiver_phone="027 529 550",
            invoice_no="VE/01/2008",
            mode_of_shipment="BY-AIR",
            goods_summary="NEPALESE COTTON & HANDICRAFT GOODS",
            quantity_unit="PCS",
            items=[
                {"description": "WOODEN MASKS", "quantity": 2, "box": "BOX NO:01"},
                {"description": "COTTON SHIRTS", "quantity": 49, "box": "BOX NO:01"},
                {"description": "YAK DOLL", "quantity": 2, "box": "BOX NO:02"},
                {"description": "WOODEN BOX", "quantity": 7, "box": "BOX NO:02"},
            ],
        )
        db.session.add(record)
        db.session.commit()
        return record.id


@pytest.fixture(scope="module")
def freight_bill(app, admin_token):
    with app.app_context():
        from app.models.user import User
        existing = Invoice.query.filter_by(invoice_number="BILL-HOUSE-1").first()
        if existing:
            return existing.id
        creator = User.query.first()
        record = Invoice(
            invoice_number="BILL-HOUSE-1",
            created_by=creator.id,
            seller_name="MOONLIGHT FREIGHT P. LTD.",
            seller_country="KATHMANDU,NEPAL",
            seller_phone="977-1-5922458",
            seller_email="moonlightfreight@example.invalid",
            buyer_name="An Example Customer",
            buyer_country="Ireland",
            awb_no="257-4850",
            account_name="Example Packers & Movers",
            currency="USD",
            fx_rate=75.65,
            items=[
                {"description": "Air freight charge 45kgs x 3.20", "total_price": 144.00},
                {"description": "awb fee + insurance fee", "total_price": 17.75},
            ],
        )
        db.session.add(record)
        db.session.commit()
        return record.id


def _sheet(path):
    return load_workbook(path).active


def _column(sheet, letter):
    return [str(c.value) for c in sheet[letter] if c.value not in (None, "")]


# ── The packing list ─────────────────────────────────────────────────────────

def test_the_packing_list_carries_the_exporters_letterhead(app, packing_list, output_dir):
    """Not ours.

    A packing list is the shipper's declaration of what is in their
    cartons. The previous generator printed "MOONLIGHT FREIGHT PVT. LTD."
    across the top, which is the wrong company on somebody else's document.
    """
    with app.app_context():
        record = db.session.get(PackingList, packing_list)
        path, _ = generate_packing_list_excel(record, output_dir)

    sheet = _sheet(path)
    assert sheet["A1"].value == "EXAMPLE EXPORTS"
    text = " ".join(str(c.value) for row in sheet.iter_rows() for c in row if c.value)
    assert "MOONLIGHT" not in text.upper(), (
        "the freight company appears on the exporter's own declaration")


def test_items_are_grouped_into_boxes(app, packing_list, output_dir):
    """Customs and the receiving agent work carton by carton."""
    with app.app_context():
        record = db.session.get(PackingList, packing_list)
        path, _ = generate_packing_list_excel(record, output_dir)

    column = _column(_sheet(path), "B")
    assert "BOX NO:01" in column and "BOX NO:02" in column
    # Each box heading comes before its own contents.
    assert column.index("BOX NO:01") < column.index("WOODEN MASKS")
    assert column.index("WOODEN MASKS") < column.index("BOX NO:02")
    assert column.index("BOX NO:02") < column.index("YAK DOLL")


def test_the_total_runs_across_every_box(app, packing_list, output_dir):
    with app.app_context():
        record = db.session.get(PackingList, packing_list)
        path, _ = generate_packing_list_excel(record, output_dir)

    sheet = _sheet(path)
    total_row = next(row for row in sheet.iter_rows()
                     if any(c.value == "TOTAL" for c in row))
    quantities = [c.value for c in total_row if isinstance(c.value, int)]
    assert quantities == [2 + 49 + 2 + 7]


def test_a_flat_list_with_no_boxes_still_renders(app, admin_token, output_dir):
    """Not every shipment is packed into labelled cartons."""
    with app.app_context():
        from app.models.user import User
        existing = PackingList.query.filter_by(pl_number="PL-HOUSE-FLAT").first()
        record = existing or PackingList(
            pl_number="PL-HOUSE-FLAT", created_by=User.query.first().id,
            sender_name="A SHIPPER", receiver_name="A CONSIGNEE",
            items=[{"description": "ONE THING", "quantity": 3}],
        )
        if existing is None:
            db.session.add(record)
            db.session.commit()
        path, _ = generate_packing_list_excel(record, output_dir)

    column = _column(_sheet(path), "B")
    assert "ONE THING" in column
    assert not any(value.startswith("BOX NO") for value in column)


def test_the_signature_line_names_the_exporter(app, packing_list, output_dir):
    with app.app_context():
        record = db.session.get(PackingList, packing_list)
        path, _ = generate_packing_list_excel(record, output_dir)
    assert any("For: EXAMPLE EXPORTS." in value for value in _column(_sheet(path), "C"))


def test_the_header_fields_are_present(app, packing_list, output_dir):
    with app.app_context():
        record = db.session.get(PackingList, packing_list)
        path, _ = generate_packing_list_excel(record, output_dir)
    sheet = _sheet(path)
    text = " ".join(str(c.value) for row in sheet.iter_rows() for c in row if c.value)
    assert "SHIPPED TO:" in text
    assert "INV NO:-VE/01/2008" in text
    assert "Mode of Shipment:-BY-AIR" in text
    assert "PACKING-LIST" in text
    for heading in ("S.NO:", "DESCRIPTION", "CMS", "QTY", "REMARKS"):
        assert heading in text


# ── The freight bill ─────────────────────────────────────────────────────────

def test_the_freight_bill_layout(app, freight_bill, output_dir):
    with app.app_context():
        record = db.session.get(Invoice, freight_bill)
        path, _ = generate_invoice_excel(record, output_dir)

    sheet = _sheet(path)
    assert sheet["A1"].value == "MOONLIGHT FREIGHT P. LTD."
    text = " ".join(str(c.value) for row in sheet.iter_rows() for c in row if c.value)
    assert "BILL NO:BILL-HOUSE-1" in text
    assert "PARTICULAR" in text
    for label in ("AWB NO.", "DAY", "MON.", "YR.", "Account"):
        assert label in text
    assert "257-4850" in text
    assert "Example Packers & Movers" in text


def test_the_bill_converts_to_rupees_at_the_stored_rate(app, freight_bill, output_dir):
    """The rate is stored, not looked up: the bill records what was charged."""
    with app.app_context():
        record = db.session.get(Invoice, freight_bill)
        path, _ = generate_invoice_excel(record, output_dir)

    sheet = _sheet(path)
    text = " ".join(str(c.value) for row in sheet.iter_rows() for c in row if c.value)
    assert "@75.65" in text
    expected = round((144.00 + 17.75) * 75.65, 2)
    assert any(c.value == expected for row in sheet.iter_rows() for c in row)


def test_the_bill_closes_with_the_amount_in_words(app, freight_bill, output_dir):
    """How a Nepali business document is expected to end."""
    with app.app_context():
        record = db.session.get(Invoice, freight_bill)
        path, _ = generate_invoice_excel(record, output_dir)
    text = " ".join(str(c.value) for row in _sheet(path).iter_rows()
                    for c in row if c.value)
    assert "In words:" in text
    assert "only." in text


def test_a_stored_wording_is_reprinted_verbatim(app, freight_bill, output_dir):
    """A reprint has to match the original, not today's generator."""
    with app.app_context():
        record = db.session.get(Invoice, freight_bill)
        record.amount_in_words = "Exactly what the office typed only."
        db.session.commit()
        path, _ = generate_invoice_excel(record, output_dir)
    text = " ".join(str(c.value) for row in _sheet(path).iter_rows()
                    for c in row if c.value)
    assert "Exactly what the office typed only." in text


# ── Amounts in words ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("amount, expected", [
    (12245, "Twelve thousand two hundred forty five only."),
    (71065, "Seventy one thousand sixty five only."),
    (0, "Zero only."),
    (1, "One only."),
    (100, "One hundred only."),
    (1000, "One thousand only."),
    (1000000, "One million only."),
])
def test_amount_in_words(amount, expected):
    """The first two are the figures from the company's own bills."""
    assert amount_in_words(amount) == expected


def test_paise_are_dropped_not_rounded_up(app):
    """The sample bills always restate the whole-rupee total."""
    assert amount_in_words(12245.99).startswith("Twelve thousand two hundred forty five")


def test_a_bad_amount_does_not_raise(app):
    assert amount_in_words(None) == ""
    assert amount_in_words("not a number") == ""


# ── Safety carried over from the generic exporter ────────────────────────────

def test_a_formula_in_a_description_is_neutralised(app, admin_token, output_dir):
    """A description beginning `=` is a formula to Excel, and these files
    are opened by customs agents and consignees."""
    with app.app_context():
        from app.models.user import User
        existing = PackingList.query.filter_by(pl_number="PL-HOUSE-FORMULA").first()
        record = existing or PackingList(
            pl_number="PL-HOUSE-FORMULA", created_by=User.query.first().id,
            sender_name="A SHIPPER", receiver_name="A CONSIGNEE",
            items=[{"description": "=HYPERLINK(\"http://evil.invalid\",\"click\")",
                    "quantity": 1}],
        )
        if existing is None:
            db.session.add(record)
            db.session.commit()
        path, _ = generate_packing_list_excel(record, output_dir)

    for row in _sheet(path).iter_rows():
        for cell in row:
            if isinstance(cell.value, str):
                assert not cell.value.startswith("="), (
                    f"{cell.coordinate} is a live formula")


def test_the_file_is_named_after_the_document(app, packing_list, output_dir):
    with app.app_context():
        record = db.session.get(PackingList, packing_list)
        path, name = generate_packing_list_excel(record, output_dir)
    assert name == "PACKING-LIST-PL-HOUSE-1.xlsx"
    assert os.path.isfile(path)


# ── Defects found when the admin screens were first wired up ────────────────

def test_a_lump_sum_charge_line_keeps_its_amount(app, admin_token, client):
    """The money bug.

    `document_data` derived every total as quantity x unit_price and
    discarded what was sent. A freight bill charges "Air freight charge
    45kgs x 3.20 = $144.00" as one amount -- the arithmetic is in the
    description -- so every charge line was being stored as zero.
    """
    from app.validation import document_data
    from app.models.user import User

    with app.app_context():
        uid = User.query.first().id
        cleaned = document_data(
            {"items": [{"description": "Air freight charge 45kgs x 3.20",
                        "total_price": 144.00}]},
            uid, "invoice")
        assert cleaned["items"][0]["total_price"] == 144.00


def test_an_itemised_line_is_still_derived_not_trusted(app, admin_token):
    """The original protection stays: a client cannot post its own total."""
    from app.validation import document_data
    from app.models.user import User

    with app.app_context():
        uid = User.query.first().id
        cleaned = document_data(
            {"items": [{"description": "Shawls", "quantity": 10,
                        "unit_price": 5.0, "total_price": 999999.0}]},
            uid, "invoice")
        assert cleaned["items"][0]["total_price"] == 50.0


def test_a_line_with_nothing_priced_is_zero_not_an_error(app, admin_token):
    from app.validation import document_data
    from app.models.user import User

    with app.app_context():
        uid = User.query.first().id
        cleaned = document_data({"items": [{"description": "To follow"}]},
                                uid, "invoice")
        assert cleaned["items"][0]["total_price"] == 0


def test_a_packing_list_line_weight_behaves_the_same_way(app, admin_token):
    from app.validation import document_data
    from app.models.user import User

    with app.app_context():
        uid = User.query.first().id
        cleaned = document_data(
            {"items": [{"description": "Carton", "total_weight_kg": 12.5}]},
            uid, "packing")
        assert cleaned["items"][0]["total_weight_kg"] == 12.5


def test_the_carton_count_means_cartons(app):
    """It defaulted to the number of line items, so a twelve-line list read
    as twelve boxes on the paperwork."""
    from app.packing_lists.routes import _carton_count

    assert _carton_count([{"box": "BOX NO:01"}, {"box": "BOX NO:01"},
                          {"box": "BOX NO:02"}]) == 2
    assert _carton_count([{"description": "unlabelled"},
                          {"description": "also unlabelled"}]) == 1
    assert _carton_count([]) == 0


def test_the_signature_does_not_double_the_full_stop(app, admin_token, output_dir):
    """"PVT. LTD." already ends in one."""
    with app.app_context():
        from app.models.user import User
        existing = PackingList.query.filter_by(pl_number="PL-HOUSE-DOT").first()
        record = existing or PackingList(
            pl_number="PL-HOUSE-DOT", created_by=User.query.first().id,
            sender_name="EXAMPLE CRAFT EXPORTS PVT. LTD.",
            receiver_name="A CONSIGNEE",
            items=[{"description": "A THING", "quantity": 1}],
        )
        if existing is None:
            db.session.add(record)
            db.session.commit()
        path, _ = generate_packing_list_excel(record, output_dir)

    text = " ".join(str(c.value) for row in _sheet(path).iter_rows()
                    for c in row if c.value)
    assert "PVT. LTD.." not in text
    assert "For: EXAMPLE CRAFT EXPORTS PVT. LTD." in text


def test_a_multi_line_shipper_address_is_split_down_the_column(
        app, admin_token, output_dir):
    """It was written into one cell with wrapping off, so a second line
    simply ran out of sight at the edge of the column."""
    with app.app_context():
        from app.models.user import User
        existing = PackingList.query.filter_by(pl_number="PL-HOUSE-ADDR").first()
        record = existing or PackingList(
            pl_number="PL-HOUSE-ADDR", created_by=User.query.first().id,
            sender_name="AN EXPORTER",
            sender_address="P.O.BOX NO:1234\nTHAMEL, KATHMANDU\nNEPAL",
            receiver_name="A CONSIGNEE",
            items=[{"description": "A THING", "quantity": 1}],
        )
        if existing is None:
            db.session.add(record)
            db.session.commit()
        path, _ = generate_packing_list_excel(record, output_dir)

    column = _column(_sheet(path), "A")
    assert "P.O.BOX NO:1234" in column
    assert "THAMEL, KATHMANDU" in column
    assert "NEPAL" in column
    # And the document below the letterhead still assembles.
    text = " ".join(str(c.value) for row in _sheet(path).iter_rows()
                    for c in row if c.value)
    assert "SHIPPED TO:" in text and "PACKING-LIST" in text
