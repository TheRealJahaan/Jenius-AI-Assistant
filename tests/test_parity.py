"""The browser assistant (web/js/assistant.js) must answer exactly like Python."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from jenius import config
from jenius.assistant import Assistant

QUESTIONS = [
    "",
    "help",
    "Give me a summary of all anomalies",
    "how many anomalies are there?",
    "Why did Chennai-Bangalore get pricier in February 2025?",
    "what happened in march 2025",
    "Which weeks need human review?",
    "anything unexplained in 2025?",
    "which anomalies are justified",
    "was the ahmedabad mumbai spike justified",
    "Why was Delhi-Jaipur flagged?",
    "Which route is the most expensive?",
    "rank the medium routes, cheapest first",
    "compare mumbai routes",
    "what does note N9 say",
    "tell me about N001 and n3 and N042",
    "How did Mumbai-Pune do in 2025?",
    "Delhi in may 2024",
    "may I ask about kolkata",
    "Tell me about Bengaluru",
    "Mumbai-Delhi",
    "diesel prices",
    "toll plaza",
    "festival surcharge",
    "qwerty",
    "Mumbai-Pune in December 2023",
]


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_python_and_javascript_answers_are_identical() -> None:
    kb = json.loads((config.WEB_DIR / "data" / "kb.json").read_text(encoding="utf-8"))
    bot = Assistant(kb)
    runner = Path(__file__).parent / "js" / "answer_cli.mjs"
    proc = subprocess.run(
        ["node", str(runner)], input=json.dumps(QUESTIONS), capture_output=True, text=True, check=True
    )
    js_answers = json.loads(proc.stdout)
    for q, js in zip(QUESTIONS, js_answers, strict=True):
        assert js == bot.answer(q), f"Python and JS disagree on {q!r}"
