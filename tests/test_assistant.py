import pytest

from jenius.assistant import HELP, Assistant


@pytest.fixture(scope="module")
def bot(kb) -> Assistant:  # type: ignore[no-untyped-def]
    return Assistant(kb)


def test_route_and_month(bot: Assistant) -> None:
    a = bot.answer("Why did Chennai-Bangalore get pricier in February 2025?")
    assert a.startswith("**Chennai-Bangalore, week of 2025-02-24**")
    assert "Justified by N001" in a and "Heavy flooding" in a


def test_city_alias_and_partial_route(bot: Assistant) -> None:
    assert "Chennai-Bangalore" in bot.answer("anything odd in Bengaluru?")


def test_summary_counts(bot: Assistant) -> None:
    a = bot.answer("give me a summary")
    assert "Anomalous route-weeks: **6**" in a
    assert "Needs human review: **2**" in a
    assert "2,940 shipments" in a


def test_needs_review(bot: Assistant) -> None:
    a = bot.answer("which weeks need human review?")
    assert a.count("Delhi-Jaipur") == 2 and "Chennai" not in a


def test_note_lookup(bot: Assistant) -> None:
    a = bot.answer("what does note N9 say?")
    assert a.startswith("**N009**") and "not a cost driver" in a
    assert "There is no note N042" in bot.answer("and n042?")


def test_ranking(bot: Assistant) -> None:
    lines = [ln for ln in bot.answer("which route is cheapest").splitlines() if ln[:1].isdigit()]
    assert len(lines) == 7 and "Mumbai-Delhi" in lines[0]


def test_route_without_anomalies_in_period(bot: Assistant) -> None:
    a = bot.answer("How did Mumbai-Pune do in March 2025?")
    assert a.startswith("**Mumbai-Pune** had no anomalous weeks in March 2025")
    assert "- 2025-03-03: ₹" in a


def test_may_is_only_a_month_in_context(bot: Assistant) -> None:
    assert Assistant.parse_period(["may", "i", "ask"]) == (None, None)
    assert Assistant.parse_period(["in", "may"]) == (5, None)
    assert Assistant.parse_period(["may", "2024"]) == (5, "2024")


def test_retrieval_fallback_and_help(bot: Assistant) -> None:
    assert "N003" in bot.answer("diesel prices")
    assert bot.answer("qwertyuiop") == HELP
    assert bot.answer("   ") == HELP
