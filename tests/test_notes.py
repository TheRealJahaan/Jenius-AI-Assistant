from jenius.notes import match_notes, note_applies, score_note
from tests.conftest import make_note


def test_flood_note_with_resolution_postscript_explains_rise(analysis) -> None:  # type: ignore[no-untyped-def]
    n001 = next(n for n in analysis.notes if n.note_id == "N001")
    sc = score_note(n001)
    assert "flooding" in sc.positive and "passable again by" in sc.negative
    assert sc.explains_cost_rise


def test_resolution_and_stability_notes_do_not_explain_rise(analysis) -> None:  # type: ignore[no-untyped-def]
    for nid in ("N005", "N006", "N007", "N008", "N009", "N010"):
        note = next(n for n in analysis.notes if n.note_id == nid)
        assert not score_note(note).explains_cost_rise, nid


def test_route_and_window_filtering() -> None:
    note = make_note("X", "2024-03-01", "Mumbai – Pune", "flooding")
    assert note_applies(note, "Mumbai-Pune", "2024-03-04")
    assert not note_applies(note, "Pune-Mumbai", "2024-03-04")
    assert not note_applies(note, "Mumbai-Pune", "2024-06-03")  # > 60 days away
    everywhere = make_note("Y", "2024-03-01", "All Routes", "diesel")
    assert note_applies(everywhere, "Any-Route", "2024-03-04")


def test_closest_explaining_note_wins() -> None:
    notes = [
        make_note("FAR", "2024-01-01", "A-B", "a surcharge was applied"),
        make_note("NEAR", "2024-02-26", "A-B", "flooding disrupted traffic"),
        make_note("NOISE", "2024-03-04", "A-B", "freight movement remained normal"),
    ]
    m = match_notes("A-B", "2024-03-04", notes)
    assert m.explained_by is not None and m.explained_by.note_id == "NEAR"
    assert m.verdict == "No (justified)"


def test_unexplained_cites_closest_candidate() -> None:
    notes = [make_note("N", "2024-03-01", "A-B", "demand remained stable")]
    m = match_notes("A-B", "2024-03-04", notes)
    assert m.explained_by is None and m.closest is not None
    assert m.verdict == "Yes"
    assert "closest note (N, 2024-03-01)" in m.reason


def test_no_candidates() -> None:
    m = match_notes("A-B", "2024-03-04", [])
    assert m.reason.startswith("No matching note found")
