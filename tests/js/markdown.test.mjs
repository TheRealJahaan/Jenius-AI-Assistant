import assert from "node:assert/strict";
import { test } from "node:test";
import { escapeHtml, renderMarkdown } from "../../web/js/markdown.js";

test("escapes HTML before adding markup", () => {
  assert.equal(escapeHtml(`<img src=x onerror="a">`), "&lt;img src=x onerror=&quot;a&quot;&gt;");
  const html = renderMarkdown("**<script>alert(1)</script>**");
  assert.ok(!html.includes("<script>"));
  assert.ok(html.includes("<strong>&lt;script&gt;"));
});

test("renders the subset the assistant emits", () => {
  const html = renderMarkdown("**Title**\n- one *two*\n- three\n\n1. first\n> quoted\nplain");
  assert.equal(
    html,
    "<p><strong>Title</strong></p><ul><li>one <em>two</em></li><li>three</li></ul>" +
      "<ol><li>first</li></ol><blockquote>quoted</blockquote><p>plain</p>",
  );
});
