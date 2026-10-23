// Turns an All Tech Stocks email (Markdown) into formatted text on the Mac clipboard,
// so it pastes into Substack with working links, bold and headings.
// Runs with macOS's built-in JavaScript: osascript -l JavaScript md-to-substack.js FILE
// Handles the pieces the email uses: ## headings, - bullets (with indented follow-on lines),
// **bold**, *italic*, [links](url) and plain paragraphs.

function esc(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function inline(s) {
  s = esc(s);
  s = s.replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, function (m, text, url) {
    return '<a href="' + url.replace(/"/g, "%22") + '">' + text + "</a>";
  });
  s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/(^|[^*])\*([^*\s][^*]*?)\*(?!\*)/g, "$1<em>$2</em>");
  return s;
}

function convert(md) {
  var lines = md.replace(/\r\n?/g, "\n").split("\n");
  var out = [], para = [], items = null;
  function flushPara() {
    if (para.length) { out.push("<p>" + para.map(inline).join("<br>") + "</p>"); para = []; }
  }
  function flushList() {
    if (items) { out.push("<ul>" + items.map(function (it) { return "<li>" + it.map(inline).join("<br>") + "</li>"; }).join("") + "</ul>"); items = null; }
  }
  lines.forEach(function (line) {
    if (/^\s*$/.test(line)) { flushPara(); flushList(); return; }
    var h = line.match(/^(#{1,3})\s+(.*)$/);
    if (h) { flushPara(); flushList(); out.push("<h" + (h[1].length + 1) + ">" + inline(h[2]) + "</h" + (h[1].length + 1) + ">"); return; }
    var b = line.match(/^[-*]\s+(.*)$/);
    if (b) { flushPara(); if (!items) items = []; items.push([b[1]]); return; }
    if (items && /^\s{2,}\S/.test(line)) { items[items.length - 1].push(line.trim()); return; }
    flushList();
    para.push(line.trim());
  });
  flushPara(); flushList();
  return out.join("\n");
}

// Plain-text fallback: links become "text (url)" so nothing is lost in a plain editor.
function plain(md) {
  return md.replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, "$1 ($2)").replace(/\*\*([^*]+)\*\*/g, "$1").replace(/(^|[^*])\*([^*\s][^*]*?)\*/g, "$1$2");
}

function run(argv) {
  ObjC.import("Foundation");
  ObjC.import("AppKit");
  var path = argv[0];
  var md = $.NSString.stringWithContentsOfFileEncodingError(path, $.NSUTF8StringEncoding, null);
  if (!md || md.isNil()) { return "Could not read " + path; }
  md = md.js;
  var html = '<meta charset="utf-8">' + convert(md);
  var pb = $.NSPasteboard.generalPasteboard;
  pb.clearContents;
  pb.setStringForType($(html), $.NSPasteboardTypeHTML);
  pb.setStringForType($(plain(md)), $.NSPasteboardTypeString);
  return "Copied. Paste it into a new Substack post.";
}

// For testing outside macOS (node): module.exports lets a test script call convert().
if (typeof module !== "undefined") { module.exports = { convert: convert, plain: plain }; }
