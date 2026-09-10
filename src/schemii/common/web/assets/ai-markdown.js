function appendInline(parent, source) {
  const pattern = /(\*\*[^*\n]+\*\*|`[^`\n]+`|\[[^\]\n]+\]\(https?:\/\/[^)\s]+\)|\*[^*\n]+\*)/g;
  let cursor = 0;
  for (const match of source.matchAll(pattern)) {
    parent.append(document.createTextNode(source.slice(cursor, match.index)));
    const token = match[0];
    let node;
    if (token.startsWith("**")) {
      node = document.createElement("strong"); node.textContent = token.slice(2, -2);
    } else if (token.startsWith("`")) {
      node = document.createElement("code"); node.textContent = token.slice(1, -1);
    } else if (token.startsWith("[")) {
      const boundary = token.indexOf("](");
      node = document.createElement("a"); node.textContent = token.slice(1, boundary);
      node.href = token.slice(boundary + 2, -1); node.target = "_blank"; node.rel = "noopener noreferrer";
    } else {
      node = document.createElement("em"); node.textContent = token.slice(1, -1);
    }
    parent.append(node); cursor = match.index + token.length;
  }
  parent.append(document.createTextNode(source.slice(cursor)));
}

function tableCells(line) {
  return line.trim().replace(/^\||\|$/g, "").split("|").map(cell => cell.trim());
}

export function renderMarkdown(source) {
  const fragment = document.createDocumentFragment();
  const lines = String(source ?? "").replace(/\r\n?/g, "\n").split("\n");
  let index = 0;
  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) { index += 1; continue; }
    if (line.trim().startsWith("```")) {
      const language = line.trim().slice(3).trim(); const body = []; index += 1;
      while (index < lines.length && !lines[index].trim().startsWith("```")) { body.push(lines[index]); index += 1; }
      index += 1;
      const pre = document.createElement("pre"); const code = document.createElement("code");
      if (language) code.dataset.language = language; code.textContent = body.join("\n"); pre.append(code); fragment.append(pre); continue;
    }
    const heading = line.match(/^(#{1,4})\s+(.+)$/);
    if (heading) {
      const node = document.createElement(`h${Math.min(heading[1].length + 2, 6)}`);
      appendInline(node, heading[2]); fragment.append(node); index += 1; continue;
    }
    if (line.includes("|") && index + 1 < lines.length && /^\s*\|?\s*:?-{3,}/.test(lines[index + 1])) {
      const table = document.createElement("table"); const head = document.createElement("thead"); const headRow = document.createElement("tr");
      for (const value of tableCells(line)) { const cell = document.createElement("th"); appendInline(cell, value); headRow.append(cell); }
      head.append(headRow); table.append(head); index += 2; const body = document.createElement("tbody");
      while (index < lines.length && lines[index].includes("|") && lines[index].trim()) {
        const row = document.createElement("tr");
        for (const value of tableCells(lines[index])) { const cell = document.createElement("td"); appendInline(cell, value); row.append(cell); }
        body.append(row); index += 1;
      }
      table.append(body); const scroll = document.createElement("div"); scroll.className = "ai-markdown-table"; scroll.tabIndex = 0; scroll.append(table); fragment.append(scroll); continue;
    }
    const listMatch = line.match(/^\s*(?:([-*+])|(\d+\.))\s+(.+)$/);
    if (listMatch) {
      const ordered = Boolean(listMatch[2]); const list = document.createElement(ordered ? "ol" : "ul");
      while (index < lines.length) {
        const itemMatch = lines[index].match(/^\s*(?:([-*+])|(\d+\.))\s+(.+)$/);
        if (!itemMatch || Boolean(itemMatch[2]) !== ordered) break;
        const item = document.createElement("li"); appendInline(item, itemMatch[3]); list.append(item); index += 1;
      }
      fragment.append(list); continue;
    }
    if (/^>\s?/.test(line)) {
      const quote = document.createElement("blockquote"); const values = [];
      while (index < lines.length && /^>\s?/.test(lines[index])) { values.push(lines[index].replace(/^>\s?/, "")); index += 1; }
      appendInline(quote, values.join(" ")); fragment.append(quote); continue;
    }
    if (/^\s*([-*_])(?:\s*\1){2,}\s*$/.test(line)) { fragment.append(document.createElement("hr")); index += 1; continue; }
    const paragraphLines = [line.trim()]; index += 1;
    while (index < lines.length && lines[index].trim() && !/^(#{1,4})\s+/.test(lines[index]) && !/^\s*(?:[-*+] |\d+\. |>|```)/.test(lines[index])) {
      if (lines[index].includes("|") && index + 1 < lines.length && /^\s*\|?\s*:?-{3,}/.test(lines[index + 1])) break;
      paragraphLines.push(lines[index].trim()); index += 1;
    }
    const paragraph = document.createElement("p"); appendInline(paragraph, paragraphLines.join(" ")); fragment.append(paragraph);
  }
  return fragment;
}
