import json

import anthropic
import httpx
from helpers import FakeClaude, demo_id, message, signup, text, thinking, tool_use
from sqlalchemy import select

from jenius.assistant.agent import REFUSAL_REPLY, ClaudeAgent
from jenius.config import load_settings
from jenius.models import LlmCall, Message


def agent_with(*responses, **settings):
    fake = FakeClaude(*responses)
    config = load_settings(jenius_env="test", llm_provider="anthropic", anthropic_api_key="test-key",
                           _env_file=None, **settings)
    return ClaudeAgent(config, client=fake), fake


def ask(client, dataset, message_text, conversation_id=None):
    return client.post("/api/chat", json={"dataset_id": dataset, "message": message_text,
                                          "conversation_id": conversation_id})


def test_guests_get_free_offline_answers(client):
    res = ask(client, demo_id(client), "Why did Chennai-Bangalore get pricier in February 2025?")
    body = res.json()
    assert res.status_code == 200 and body["mode"] == "offline" and body["conversation_id"] is None
    assert "N001" in body["reply"] and "Justified" in body["reply"]


def test_quick_answers_cover_carriers_and_routes(client):
    demo = demo_id(client)
    carriers = ask(client, demo, "Which transporters are the most expensive per tonne-km?").json()["reply"]
    assert carriers.startswith("**Transporters by average cost per tonne-km** (all routes)") and "| Transporter |" in carriers
    routes = ask(client, demo, "Compare the average cost per tonne-km across all routes").json()["reply"]
    assert "| Chennai-Bangalore |" in routes and "| Delhi-Jaipur |" in routes


def test_quick_answers_understand_exact_weeks(client):
    reply = ask(client, demo_id(client), "Explain the Chennai-Bangalore anomaly in the week of 2025-03-12").json()["reply"]
    assert reply.startswith("**Chennai-Bangalore**, week of 2025-03-10") and "Found" not in reply


def test_claude_uses_the_database_tools(make_client, database):
    agent, fake = agent_with(
        message(thinking(), tool_use("list_anomalies", route="chennai to bangalore", status="all"), stop="tool_use"),
        message(text("Chennai-Bangalore spiked in late Feb 2025 because of flooding [N001]."), output_tokens=50),
    )
    client = make_client(agent=agent)
    signup(client)
    res = ask(client, demo_id(client), "Why did Chennai-Bangalore get pricier?")
    body = res.json()
    assert res.status_code == 200, res.text
    assert body["mode"] == "ai" and body["tools_used"] == ["list_anomalies"] and "[N001]" in body["reply"]
    assert body["remaining_today"] == 24

    first, second = fake.requests
    assert first["model"] == "claude-opus-5-5" and first["fallbacks"] == "default"
    assert first["betas"] == ["server-side-fallback-2026-07-01"]
    assert first["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "Demo: FreightTiger case study" in first["system"][1]["text"]
    tool_result = second["messages"][-1]["content"][0]
    assert tool_result["type"] == "tool_result" and "is_error" not in tool_result
    rows = json.loads(tool_result["content"])["anomalies"]
    assert {r["week_of"] for r in rows} == {"2025-02-24", "2025-03-03", "2025-03-10"}

    with database.sessions() as db:
        calls = db.scalars(select(LlmCall)).all()
        assert len(calls) == 2
        assert float(calls[0].cost_usd) == (1000 * 4 + 200 * 20) / 1_000_000  # Opus 5.5 prices
        stored = db.scalars(select(Message).order_by(Message.seq)).all()
        assert [m.role for m in stored] == ["user", "assistant", "user", "assistant"]
        assert stored[1].content[0] == {"type": "thinking", "thinking": "", "signature": "sig-abc"}


def test_history_is_replayed_unchanged(make_client):
    agent, fake = agent_with(
        message(thinking(), text("There are 6 anomalies.")),
        message(text("Two need review.")),
    )
    client = make_client(agent=agent)
    signup(client)
    demo = demo_id(client)
    conv = ask(client, demo, "How many anomalies?").json()["conversation_id"]
    ask(client, demo, "And how many need review?", conv)
    first_turn = fake.requests[0]["messages"] + [{"role": "assistant", "content": [
        {"type": "thinking", "thinking": "", "signature": "sig-abc"}, {"type": "text", "text": "There are 6 anomalies."}]}]
    assert fake.requests[1]["messages"][:2] == first_turn
    assert fake.requests[1]["messages"][2] == {"role": "user", "content": "And how many need review?"}
    shown = client.get(f"/api/conversations/{conv}").json()
    assert [(m["role"], m["text"]) for m in shown] == [
        ("user", "How many anomalies?"), ("assistant", "There are 6 anomalies."),
        ("user", "And how many need review?"), ("assistant", "Two need review.")]


def test_bad_tool_input_goes_back_to_claude_as_an_error(make_client):
    agent, fake = agent_with(
        message(tool_use("route_weekly_costs", route="Atlantis-Mars"), stop="tool_use"),
        message(text("That route isn't in your data.")),
    )
    client = make_client(agent=agent)
    signup(client)
    ask(client, demo_id(client), "Costs on Atlantis-Mars?")
    result = fake.requests[1]["messages"][-1]["content"][0]
    assert result["is_error"] is True and "Routes: Ahmedabad-Mumbai" in result["content"]


def test_refusals_are_shown_but_not_replayed(make_client):
    agent, fake = agent_with(message(stop="refusal"), message(text("Sure: 6 anomalies.")))
    client = make_client(agent=agent)
    signup(client)
    demo = demo_id(client)
    first = ask(client, demo, "something odd").json()
    assert first["reply"] == REFUSAL_REPLY
    ask(client, demo, "How many anomalies?", first["conversation_id"])
    assert fake.requests[1]["messages"] == [{"role": "user", "content": "How many anomalies?"}]


def test_daily_quota_falls_back_to_quick_answers(make_client):
    agent, fake = agent_with(message(text("Answer one.")))
    client = make_client(agent=agent, free_daily_messages=1)
    user = signup(client)
    assert user["limits"]["daily_messages"] == 1
    demo = demo_id(client)
    assert ask(client, demo, "First question").json()["mode"] == "ai"
    second = ask(client, demo, "Give me a summary").json()
    assert second["mode"] == "offline" and "today's 1 Claude messages" in second["notice"]
    assert len(fake.requests) == 1  # Claude wasn't called again
    assert client.get("/api/auth/me").json()["limits"]["used_today"] == 1


def test_claude_outage_falls_back_to_quick_answers(make_client):
    outage = anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"))
    agent, _ = agent_with(outage)
    client = make_client(agent=agent)
    signup(client)
    res = ask(client, demo_id(client), "Give me a summary").json()
    assert res["mode"] == "offline" and "unavailable" in res["notice"] and "Anomaly summary" in res["reply"]
    assert res["remaining_today"] == 25  # nothing was spent, so nothing was counted


def test_conversations_are_private(make_client):
    agent, _ = agent_with(message(text("Hi.")))
    alice, bob = make_client(agent=agent), make_client(agent=agent)
    signup(alice, "alice@example.com", "Alice")
    signup(bob, "bob@example.com", "Bob")
    conv = ask(alice, demo_id(alice), "hello").json()["conversation_id"]
    assert bob.get(f"/api/conversations/{conv}").status_code == 404
    assert ask(bob, demo_id(bob), "hello", conv).status_code == 404


def test_a_crashing_tool_is_reported_to_claude(make_client, monkeypatch):
    def boom(self):
        raise RuntimeError("database hiccup")
    monkeypatch.setattr("jenius.assistant.tools.ToolBox.tool_get_overview", boom)
    agent, fake = agent_with(message(tool_use("get_overview"), stop="tool_use"), message(text("Sorry, try again.")))
    client = make_client(agent=agent)
    signup(client)
    assert ask(client, demo_id(client), "overview please").status_code == 200
    result = fake.requests[1]["messages"][-1]["content"][0]
    assert result["is_error"] is True and "failed on the server" in result["content"]
