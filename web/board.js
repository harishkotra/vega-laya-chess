/* Chess board rendering with move arrows. No library, no dependency, no build.

   The app never generates a move and never applies one: it draws the FEN it was
   given, and every position it shows comes out of the data file, where the
   ground truth script computed it and the verifier checked it against a real
   chess library.

   Pieces, coordinates and arrows are all drawn in one SVG layer whose viewBox
   is 8x8 with one unit per square, so the board scales to any pixel size with
   no fixed font sizes anywhere. Pieces use the solid unicode shapes for both
   colours, filled and stroked in CSS: the outline glyphs look thin and washed
   out at board size. */

window.VegaBoard = (function () {
  const SOLID = {
    K: "\u265A", Q: "\u265B", R: "\u265C", B: "\u265D", N: "\u265E", P: "\u265F"
  };
  const FILES = "abcdefgh";
  const PIECE_FONT = '"Apple Symbols", "Segoe UI Symbol", "DejaVu Sans", "Noto Sans Symbols 2", Symbola, serif';

  function escapeHtml(value) {
    return String(value === null || value === undefined ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function parsePlacement(fen) {
    const placement = String(fen || "").split(" ")[0];
    const rows = placement.split("/");
    if (rows.length !== 8) return null;
    return rows.map(function (row) {
      const out = [];
      for (const ch of row) {
        if (/[1-8]/.test(ch)) {
          for (let i = 0; i < Number(ch); i += 1) out.push(null);
        } else {
          out.push(ch);
        }
      }
      return out.length === 8 ? out : null;
    });
  }

  function squareName(rowIndex, colIndex) {
    return FILES[colIndex] + String(8 - rowIndex);
  }

  function squareCenter(name) {
    const file = FILES.indexOf(String(name || "").charAt(0));
    const rank = Number(String(name || "").charAt(1));
    if (file < 0 || !(rank >= 1 && rank <= 8)) return null;
    return { x: file + 0.5, y: 8 - rank + 0.5 };
  }

  function uciSquares(uci) {
    if (!uci || uci.length < 4) return [];
    return [uci.slice(0, 2), uci.slice(2, 4)];
  }

  function isLight(rowIndex, colIndex) {
    return (rowIndex + colIndex) % 2 === 0;
  }

  function hash(text) {
    let value = 0;
    for (let i = 0; i < text.length; i += 1) {
      value = (value << 5) - value + text.charCodeAt(i);
      value |= 0;
    }
    return Math.abs(value).toString(36);
  }

  function arrowMarkup(arrows, idPrefix) {
    const defs = [];
    const body = [];
    arrows.forEach(function (arrow, index) {
      const from = squareCenter(arrow.from);
      const to = squareCenter(arrow.to);
      if (!from || !to) return;
      const markerId = idPrefix + "-h" + index;
      defs.push('<marker id="' + markerId + '" markerWidth="0.34" markerHeight="0.34" refX="0" refY="0.17" ' +
        'orient="auto" markerUnits="userSpaceOnUse"><path d="M0,0 L0.34,0.17 L0,0.34 z" fill="' + arrow.color +
        '"/></marker>');
      const dx = to.x - from.x, dy = to.y - from.y;
      const length = Math.sqrt(dx * dx + dy * dy) || 1;
      const ux = dx / length, uy = dy / length;
      const endX = to.x - ux * 0.36, endY = to.y - uy * 0.36;
      body.push('<circle cx="' + from.x + '" cy="' + from.y + '" r="0.12" fill="' + arrow.color +
        '" fill-opacity="0.85"/>');
      body.push('<line x1="' + from.x + '" y1="' + from.y + '" x2="' + endX + '" y2="' + endY +
        '" stroke="' + arrow.color + '" stroke-width="0.1" stroke-linecap="round" class="arrow-line" ' +
        'marker-end="url(#' + markerId + ')"/>');
    });
    return { defs: defs.join(""), body: body.join("") };
  }

  /* render(container, fen, opts)

     opts.pickUci    the move being shown (blue, or violet when it is also the answer)
     opts.truthUcis  the verified mating moves (green)
     opts.marks      [{square, kind}] extra square tints, kind "after"
     opts.coord      draw file and rank labels, default true
     opts.mate       show a checkmate badge over the board
     opts.caption    small label under the board                                  */
  function render(container, fen, options) {
    const opts = options || {};
    if (!container) return;
    const grid = parsePlacement(fen);
    if (!grid) {
      container.innerHTML = '<p class="muted">Unreadable FEN.</p>';
      return;
    }
    const pick = uciSquares(opts.pickUci);
    const truth = [];
    (opts.truthUcis || []).forEach(function (uci) {
      uciSquares(uci).forEach(function (square) { truth.push(square); });
    });
    const marks = {};
    (opts.marks || []).forEach(function (mark) { marks[mark.square] = mark.kind; });

    let squares = "";
    let coords = "";
    let pieces = "";
    for (let row = 0; row < 8; row += 1) {
      for (let col = 0; col < 8; col += 1) {
        const name = squareName(row, col);
        const light = isLight(row, col);
        const classes = ["sq", light ? "light" : "dark"];
        const isPick = pick.indexOf(name) !== -1;
        const isTruth = truth.indexOf(name) !== -1;
        if (isPick && isTruth) classes.push("pick", "truth", "both");
        else if (isPick) classes.push("pick");
        else if (isTruth) classes.push("truth");
        if (marks[name]) classes.push(marks[name]);
        squares += '<div class="' + classes.join(" ") + '"></div>';

        if (opts.coord !== false) {
          const dark = light ? "on-light" : "on-dark";
          if (row === 7) {
            coords += '<text class="coord ' + dark + '" x="' + (col + 0.9) + '" y="7.9" font-size="0.2" ' +
              'text-anchor="end">' + FILES[col] + "</text>";
          }
          if (col === 0) {
            coords += '<text class="coord ' + dark + '" x="0.1" y="' + (row + 0.26) + '" font-size="0.2">' +
              (8 - row) + "</text>";
          }
        }

        const piece = grid[row][col];
        if (piece) {
          const color = piece === piece.toUpperCase() ? "w" : "b";
          pieces += '<text class="pc ' + color + '" x="' + (col + 0.5) + '" y="' + (row + 0.815) +
            '" font-size="0.88" text-anchor="middle">' + SOLID[piece.toUpperCase()] + "</text>";
        }
      }
    }

    const arrows = [];
    const shared = opts.pickUci && (opts.truthUcis || []).indexOf(opts.pickUci) !== -1;
    if (shared) {
      arrows.push({ from: opts.pickUci.slice(0, 2), to: opts.pickUci.slice(2, 4), color: "#7c3aed" });
    } else {
      if (opts.pickUci) {
        arrows.push({ from: opts.pickUci.slice(0, 2), to: opts.pickUci.slice(2, 4), color: "#2563eb" });
      }
      (opts.truthUcis || []).forEach(function (uci) {
        arrows.push({ from: uci.slice(0, 2), to: uci.slice(2, 4), color: "#15803d" });
      });
    }
    const prefix = "a" + hash(fen + (opts.pickUci || "") + (opts.caption || "") + (opts.truthUcis || []).join(""));
    const arrow = arrowMarkup(arrows, prefix);

    container.innerHTML =
      '<div class="board-frame">' +
      '<div class="board-grid">' + squares + "</div>" +
      '<svg class="board-overlay" viewBox="0 0 8 8" preserveAspectRatio="none" aria-hidden="true">' +
      "<defs>" + arrow.defs + "</defs>" + coords + pieces + arrow.body + "</svg>" +
      (opts.mate ? '<div class="mate-badge">checkmate</div>' : "") +
      "</div>" +
      (opts.caption ? '<div class="board-caption">' + escapeHtml(opts.caption) + "</div>" : "");
  }

  function legend(pickSan, truthSans, shared) {
    const parts = [];
    if (pickSan) {
      parts.push('<span><i style="background:' + (shared ? "#7c3aed" : "#2563eb") + '"></i>' +
        "the model played <b>" + escapeHtml(pickSan) + "</b></span>");
    }
    if (truthSans && truthSans.length) {
      parts.push('<span><i style="background:#15803d"></i>verified mate: <b>' +
        escapeHtml(truthSans.join(" or ")) + "</b></span>");
    }
    return parts.length ? '<div class="legend">' + parts.join("") + "</div>" : "";
  }

  return { render: render, legend: legend, escapeHtml: escapeHtml, uciSquares: uciSquares };
})();
