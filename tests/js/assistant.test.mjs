import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { Assistant, HELP, groupDigits, monthLabel } from "../../web/js/assistant.js";

const kb = JSON.parse(readFileSync(new URL("../../web/data/kb.json", import.meta.url), "utf-8"));
const bot = new Assistant(kb);

test("helpers", () => {
  assert.equal(groupDigits(2940), "2,940");
  assert.equal(groupDigits(1234567), "1,234,567");
  assert.equal(monthLabel("2025-02-24"), "Feb 2025");
});

test("answers a route + month question from the data", () => {
  const a = bot.answer("Why did Chennai-Bangalore get pricier in February 2025?");
  assert.match(a, /^\*\*Chennai-Bangalore, week of 2025-02-24\*\*/);
  assert.match(a, /Justified by N001/);
});

test("lists weeks needing review", () => {
  const a = bot.answer("which weeks need review");
  assert.equal(a.match(/Delhi-Jaipur/g).length, 2);
});

test("falls back to help for nonsense", () => {
  assert.equal(bot.answer("zzzz"), HELP);
});
