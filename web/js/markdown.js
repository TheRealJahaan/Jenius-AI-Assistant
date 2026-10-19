// Minimal, safe Markdown renderer for assistant answers.
//
// Supports exactly what the assistant emits: paragraphs, **bold**, *italic*,
// "- " bullet lists, "1. " numbered lists and "> " quotes. All text is
// HTML-escaped before any markup is added, so answers can never inject HTML.

const ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

export function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (c) => ESCAPES[c]);
}

function inline(text) {
  return escapeHtml(text)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*(?!\s)(.+?)\*(?!\*)/g, "$1<em>$2</em>");
}

export function renderMarkdown(source) {
  const html = [];
  let list = null; // "ul" | "ol" | null
  let para = [];

  const flushPara = () => {
    if (para.length) html.push(`<p>${para.map(inline).join("<br>")}</p>`);
    para = [];
  };
  const closeList = () => {
    if (list) html.push(`</${list}>`);
    list = null;
  };
  const openList = (kind) => {
    if (list !== kind) {
      closeList();
      html.push(`<${kind}>`);
      list = kind;
    }
  };

  for (const line of String(source).split("\n")) {
    let m;
    if (!line.trim()) {
      flushPara();
      closeList();
    } else if ((m = line.match(/^- (.*)$/))) {
      flushPara();
      openList("ul");
      html.push(`<li>${inline(m[1])}</li>`);
    } else if ((m = line.match(/^\d+\. (.*)$/))) {
      flushPara();
      openList("ol");
      html.push(`<li>${inline(m[1])}</li>`);
    } else if ((m = line.match(/^> ?(.*)$/))) {
      flushPara();
      closeList();
      html.push(`<blockquote>${inline(m[1])}</blockquote>`);
    } else {
      closeList();
      para.push(line);
    }
  }
  flushPara();
  closeList();
  return html.join("");
}
