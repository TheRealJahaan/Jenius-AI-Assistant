// Reads a JSON array of questions on stdin and prints the JS assistant's
// answers as a JSON array. Used by tests/test_parity.py.
import { readFileSync } from "node:fs";
import { Assistant } from "../../web/js/assistant.js";

const kb = JSON.parse(readFileSync(new URL("../../web/data/kb.json", import.meta.url), "utf-8"));
const questions = JSON.parse(readFileSync(0, "utf-8"));
const bot = new Assistant(kb);
process.stdout.write(JSON.stringify(questions.map((q) => bot.answer(q))));
