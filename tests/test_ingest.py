import pytest

from jenius.ingest import UploadError, parse_notes, parse_shipments

HEADER = "shipment_id,origin,destination,route_type,material,quantity_tonnes,distance_km,freight_cost_inr,shipment_date,transporter\n"


def test_good_rows_parse():
    rows = parse_shipments((HEADER + "S1,Mumbai,Pune,Short,Steel,12.5,150,\"6,000\",2025-01-06,Om Sai\n").encode(), 10)
    assert rows[0]["route"] == "Mumbai-Pune" and rows[0]["freight_cost_inr"] == 6000.0


def test_bad_rows_are_reported_with_line_numbers():
    raw = (HEADER + "S1,Mumbai,Pune,Short,Steel,abc,150,6000,2025-01-06,X\n"
                    "S2,Mumbai,Pune,Short,Steel,10,0,6000,2025-01-06,X\n"
                    "S3,Mumbai,Pune,Short,Steel,10,150,6000,06/01/2025,X\n").encode()
    with pytest.raises(UploadError) as err:
        parse_shipments(raw, 10)
    problems = " ".join(err.value.problems)
    assert "Line 2: quantity_tonnes must be a number" in problems
    assert "Line 3: distance_km must be greater than 0" in problems
    assert "Line 4: shipment_date must be a date" in problems


def test_missing_columns():
    with pytest.raises(UploadError, match="missing columns: distance_km"):
        parse_shipments(b"shipment_id,origin,destination,route_type,quantity_tonnes,freight_cost_inr,shipment_date\n", 10)


def test_row_limit():
    raw = (HEADER + "S1,Mumbai,Pune,Short,Steel,10,150,6000,2025-01-06,X\n" * 3).encode()
    with pytest.raises(UploadError, match="more than 2 rows"):
        parse_shipments(raw, 2)


def test_not_utf8():
    with pytest.raises(UploadError, match="UTF-8"):
        parse_shipments(HEADER.encode() + "S1,Münch".encode("latin-1"), 10)


def test_notes_duplicate_ids_and_blank_rows():
    ok = parse_notes(b"note_id,date,applies_to,note\nN1,2025-01-01,All Routes,Diesel rose\n,,,\n")
    assert [n["note_code"] for n in ok] == ["N1"]
    with pytest.raises(UploadError):
        parse_notes(b"note_id,date,applies_to,note\nN1,2025-01-01,All Routes,a\nN1,2025-01-02,All Routes,b\n")
