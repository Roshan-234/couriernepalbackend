"""Generated workbooks must not carry a customer's text through as a formula."""
import pytest

from app.utils.excel import _safe


@pytest.mark.parametrize("payload", [
    "=HYPERLINK(\"http://evil.test\",\"click\")",
    "=1+1",
    "+1+1",
    "-1+1",
    "@SUM(A1:A9)",
    "=cmd|'/c calc'!A0",
    "\t=1+1",
    "\r=1+1",
])
def test_formula_leads_are_neutralised(payload):
    out = _safe(payload)
    assert out.startswith("'"), f"{payload!r} was left executable"
    assert out[1:] == payload, "the original text must survive, only escaped"


@pytest.mark.parametrize("value", [
    "Ram Bahadur Shrestha",
    "Nayabazar, Kathmandu",
    "",
    "Item #3 (fragile)",
    "a-b",          # a dash inside the string is fine; only a leading one is not
])
def test_ordinary_text_is_untouched(value):
    assert _safe(value) == value


@pytest.mark.parametrize("value", [0, 1, -5, 3.14, None, True])
def test_non_strings_pass_through(value):
    """Totals and dates must stay typed, or the sheet stops calculating."""
    assert _safe(value) is value


def test_cell_writer_escapes(tmp_path):
    """The escape has to happen in the writer, not just the helper."""
    from openpyxl import Workbook, load_workbook
    from app.utils.excel import _cell

    wb = Workbook()
    ws = wb.active
    _cell(ws, 1, 1, "=1+1")
    _cell(ws, 2, 1, "Normal name")
    _cell(ws, 3, 1, 42)

    path = tmp_path / "check.xlsx"
    wb.save(path)

    loaded = load_workbook(path).active
    assert loaded.cell(1, 1).data_type != "f", "cell was stored as a formula"
    assert loaded.cell(2, 1).value == "Normal name"
    assert loaded.cell(3, 1).value == 42
