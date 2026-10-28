"""Helpers the tests share: signing up, and a scripted stand-in for the Anthropic client."""
import copy
from types import SimpleNamespace

from anthropic.types.beta import BetaMessage

STRONG_PASSWORD = "correct-horse-battery"


def signup(client, email="sam@example.com", name="Sam Rao", password=STRONG_PASSWORD):
    res = client.post("/api/auth/signup", json={"name": name, "email": email, "password": password})
    assert res.status_code == 201, res.text
    return res.json()


def demo_id(client) -> str:
    return next(d["id"] for d in client.get("/api/datasets").json() if d["is_demo"])


# ── a scripted stand-in for the Anthropic client ────────────────────────────

def message(*blocks, stop="end_turn", model="claude-opus-5-5", input_tokens=1000, output_tokens=200, cache_read=0):
    return BetaMessage.model_validate({
        "id": "msg_test", "type": "message", "role": "assistant", "model": model, "content": list(blocks),
        "stop_reason": stop, "stop_sequence": None,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens, "cache_read_input_tokens": cache_read},
    })


def text(t):
    return {"type": "text", "text": t}


def tool_use(name, tool_id="toolu_1", **args):
    return {"type": "tool_use", "id": tool_id, "name": name, "input": args}


def thinking():
    return {"type": "thinking", "thinking": "", "signature": "sig-abc"}


class FakeClaude:
    """Returns the scripted responses in order and records every request."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.requests.append(copy.deepcopy(kwargs))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item
