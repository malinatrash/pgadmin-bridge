// Classifies the bridge's log lines and highlights SQL in them.
import { esc, html, raw } from "./dom.js";

const SQL = /^(\S+)\s+(\d+)ms (.+?) \| ([\s\S]*)$/;
const KEYWORDS = new Set(("select from where and or not in is null as on join left right inner outer full cross group by order " +
  "having limit offset insert into values update set delete returning with create alter drop table index view begin commit " +
  "rollback distinct union all case when then else end exists like ilike between asc desc true false show explain analyze " +
  "grant revoke truncate do language function schema database primary key references default cast over partition").split(" "));
const TOKENS = /('(?:[^']|'')*')|(--[^\n]*|\/\*[\s\S]*?\*\/)|\b(\d+(?:\.\d+)?)\b|([A-Za-z_][A-Za-z0-9_]*)|([\s\S])/g;

/** -> {time, kind: sql|error|http|session|system, text, sql?} */
export function parse(line) {
  const time = line.slice(0, 8);
  const text = line.slice(9);
  if (text.startsWith("  http ")) return { time, kind: "http", text: text.slice(7) };
  if (/^\[[+~x]\]/.test(text)) return { time, kind: "session", text: text.slice(4) };
  if (text.startsWith("[!]")) return { time, kind: "error", text: text.slice(4) };
  const m = SQL.exec(text);
  if (m) {
    const [, target, ms, tag, sql] = m;
    return { time, kind: tag.startsWith("ERROR") ? "error" : "sql", text, sql: { target, ms: +ms, tag, sql } };
  }
  return { time, kind: "system", text };
}

export function highlight(sql, query = "") {
  let out = "";
  for (const [, str, comment, num, word, other] of sql.matchAll(TOKENS)) {
    if (str) out += `<span class="tok-s">${esc(str)}</span>`;
    else if (comment) out += `<span class="tok-c">${esc(comment)}</span>`;
    else if (num) out += `<span class="tok-n">${num}</span>`;
    else if (word) out += KEYWORDS.has(word.toLowerCase()) ? `<span class="tok-k">${word}</span>` : word;
    else out += esc(other);
  }
  return raw(query ? mark(out, query) : out);
}

/** Wraps matches of query in <mark>, only in text between tags. */
export function mark(htmlText, query) {
  const needle = esc(query).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return String(htmlText).replace(/(^|>)([^<]+)/g, (_, gt, text) => gt + text.replace(new RegExp(needle, "gi"), "<mark>$&</mark>"));
}

/** Rich body of a parsed SQL line: target, duration (colored by slowness), tag and the statement. */
export function sqlBody({ target, ms, tag, sql }, query = "") {
  const speed = ms >= 5000 ? "very-slow" : ms >= 1000 ? "slow" : "";
  return html`<span class="target">${target}</span> <span class="ms ${speed}">${ms}ms</span> <span class="tag">${tag}</span> ${highlight(sql, query)}`;
}
