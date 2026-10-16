/* Structural test for the board renderer, run with node:
 *
 *     node tests/board_render.mjs
 *
 * The board is the part of this project a screenshot cannot be trusted for, and
 * it is the part a reviewer cannot click through in a pull request. So it is
 * checked structurally instead: the right number of squares, one arrow per
 * move, one glyph per piece, coordinates, and a mate badge only when asked.
 *
 * It reads a real position out of data/positions/, so it also proves the viewer
 * can draw what the data files actually contain.
 */

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "..");

const boardSource = fs.readFileSync(path.join(root, "web", "board.js"), "utf8");
const Board = new Function("window", boardSource + "\nreturn window.VegaBoard;")({});

const failures = [];
function check(name, condition, detail) {
  if (condition) {
    console.log("  ok   " + name);
  } else {
    failures.push(name + (detail ? ": " + detail : ""));
    console.log("  FAIL " + name + (detail ? ": " + detail : ""));
  }
}

const rows = fs.readFileSync(path.join(root, "data", "positions", "demo-mate2.jsonl"), "utf8")
  .split("\n").filter(Boolean).map(function (line) { return JSON.parse(line); });
const row = rows[0];
const key = row.options.find(function (option) { return option.is_key; });

console.log("board renderer, on " + row.id);

const container = { innerHTML: "" };
Board.render(container, row.fen, { pickUci: key.uci, truthUcis: [], caption: "the position" });
const html = container.innerHTML;

const squares = (html.match(/class="sq /g) || []).length;
check("64 squares", squares === 64, "got " + squares);

const pieces = (html.match(/class="pc /g) || []).length;
const inFen = [...row.fen.split(" ")[0]].filter(function (ch) { return /[a-zA-Z]/.test(ch); }).length;
check("one glyph per piece", pieces === inFen, pieces + " glyphs vs " + inFen + " pieces in the FEN");

check("svg overlay present", html.includes('class="board-overlay"'));
check("one arrow line", (html.match(/class="arrow-line"/g) || []).length === 1);
check("arrow has a head marker", html.includes("marker-end=\"url(#"));
check("arrow starts on the from square", html.includes('cx="' + (key.uci.charCodeAt(0) - 97 + 0.5)));
check("file coordinates drawn", html.includes(">a</text>") && html.includes(">h</text>"));
check("rank coordinates drawn", html.includes(">8</text>") && html.includes(">1</text>"));
check("no mate badge unless asked", !html.includes("mate-badge"));
check("caption rendered", html.includes('class="board-caption"'));

const both = { innerHTML: "" };
Board.render(both, row.fen, { pickUci: key.uci, truthUcis: [key.uci] });
check("pick equal to the answer draws one arrow", (both.innerHTML.match(/class="arrow-line"/g) || []).length === 1);
check("pick equal to the answer is violet", both.innerHTML.includes("#7c3aed"));

const two = { innerHTML: "" };
Board.render(two, row.fen, { pickUci: row.options.find(function (o) { return !o.is_key; }).uci, truthUcis: [key.uci] });
check("two different moves draw two arrows", (two.innerHTML.match(/class="arrow-line"/g) || []).length === 2);

const after = { innerHTML: "" };
Board.render(after, key.fen_after, { mate: true, marks: [{ square: key.uci.slice(2, 4), kind: "after" }] });
check("mate badge shown when asked", after.innerHTML.includes("mate-badge"));
check("destination square marked", after.innerHTML.includes("sq after") || after.innerHTML.includes("after\""));

const bad = { innerHTML: "" };
Board.render(bad, "not a fen", {});
check("unreadable FEN does not throw", bad.innerHTML.includes("Unreadable FEN"));

console.log("");
if (failures.length) {
  console.log(failures.length + " check(s) failed");
  process.exit(1);
}
console.log("all board checks passed");
