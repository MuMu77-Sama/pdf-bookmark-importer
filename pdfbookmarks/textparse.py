"""Parse bookmark outline *text* into a flat ``(depth, title, page)`` list.

The parser is intentionally forgiving, because "a text file of bookmarks" is
written differently by everybody.  It understands, and freely mixes:

* indentation outlines (spaces or tabs)
* Markdown headings (``#`` .. ``######``) and nested ``-``/``*``/``+`` lists
* numbered outlines (``1``, ``1.1``, ``1.1.1``) where the number of components
  carries the level
* Chinese headings (``第一章`` -> level 0, ``第二节`` -> level 1, ``一、``,
  ``（一）``)
* a trailing page number separated by whitespace, a dot leader, ``|`` or a dash
* tab- or comma-separated ``title, page`` records

A trailing number is only treated as a page number when something separates it
from the title, so ``方法2`` stays a title while ``方法 2`` becomes title
``方法`` on page 2.
"""

from __future__ import annotations

import re

__all__ = ["parse_bookmark_text", "ParseResult", "ParseIssue", "roman_to_int"]

# --- trailing page number -------------------------------------------------
#
# Every whitespace class here is \s, not [ \t].  Chinese and Japanese outlines
# very often separate a heading from its number with the *ideographic space*
# U+3000 ("10.1\u3000引言"), and sometimes with U+00A0; [ \t] matches neither,
# which silently flattens the whole outline.
#
# "strict" requires an explicit separator (dot leader, bar or dash) and is used
# for Markdown headings, where "# Chapter 1" must stay a title.
_PAGE_TAIL_STRICT_RE = re.compile(
    r"""^(?P<title>.*?)
        (?:
            \s*[.·…]{2,}\s*        # dot leader  ......12
          | \s*[|｜]\s*            # vertical bar
          | \s*[-–—]{1,2}\s+       # dash followed by space
        )
        (?P<page>\d{1,5})\s*$""",
    re.VERBOSE,
)

# "loose" additionally accepts a plain space: "Introduction 12".
_PAGE_TAIL_RE = re.compile(
    r"""^(?P<title>.*?)
        (?:
            \s*[.·…]{2,}\s*        # dot leader  ......12
          | \s*[|｜]\s*            # vertical bar
          | \s*[-–—]{1,2}\s+       # dash followed by space
          | \s+                    # plain whitespace, half- or full-width
        )
        (?P<page>\d{1,5})\s*$""",
    re.VERBOSE,
)

_ROMAN_TAIL_STRICT_RE = re.compile(
    r"""^(?P<title>.*?)
        (?:
            \s*[.·…]{2,}\s*
          | \s*[|｜]\s*
          | \s*[-–—]{1,2}\s+
        )
        (?P<page>[ivxlcdmIVXLCDM]{1,7})\s*$""",
    re.VERBOSE,
)

_ROMAN_TAIL_RE = re.compile(
    r"""^(?P<title>.*?)
        (?:
            \s*[.·…]{2,}\s*
          | \s*[|｜]\s*
          | \s*[-–—]{1,2}\s+
          | \s+
        )
        (?P<page>[ivxlcdmIVXLCDM]{1,7})\s*$""",
    re.VERBOSE,
)

# An explicitly marked page: "p12", "P12", "第 12 页", "12 页".  The marker must
# be a separate token, so "Deep 2" is not read as page 2 of "Dee".
_EXPLICIT_PAGE_RES = (
    re.compile(r"^(?P<title>.*?)\s*第\s*(?P<page>\d{1,5})\s*页\s*$"),
    re.compile(r"^(?P<title>.+?)\s+(?:[pP]\.?|[pP]age)\s*(?P<page>\d{1,5})\s*$"),
    re.compile(r"^(?P<title>.+?)\s+(?P<page>\d{1,5})\s*页\s*$"),
)

# --- structure markers ----------------------------------------------------
_MD_HEADING_RE = re.compile(r"^(\s*)(#{1,6})\s+(.*)$")
_MD_LIST_RE = re.compile(r"^(\s*)(?:[-*+]|\d+[.)])\s+(.*)$")
_NUMERIC_RE = re.compile(r"^(\d+(?:[.．]\d+)*)[.)、]?\s+(.*)$")
_CN_CHAPTER_RE = re.compile(r"^第\s*([一二三四五六七八九十百千零〇\d]+)\s*([章节篇部回])")
_CN_ITEM_RE = re.compile(r"^[（(]\s*([一二三四五六七八九十百零〇]+)\s*[）)]\s*(.*)$")
_CN_PLAIN_RE = re.compile(r"^([一二三四五六七八九十百零〇]+)[、.]\s*(.*)$")
_CSV_RE = re.compile(r"^(?P<title>.+?)\s*,\s*(?P<page>\d{1,5})\s*$")
_COMMENT_RES = (re.compile(r"^\s*//"), re.compile(r"^\s*;"), re.compile(r"^\s*#!\s"))

_ROMAN_VALUES = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}


class ParseIssue:
    """A recoverable problem found while parsing."""

    __slots__ = ("line_no", "text", "message")

    def __init__(self, line_no: int, text: str, message: str):
        self.line_no = line_no
        self.text = text
        self.message = message

    def __repr__(self):  # pragma: no cover - debugging aid
        return "line %d: %s (%s)" % (self.line_no, self.message, self.text)

    def __str__(self):
        return "line %d: %s -- %r" % (self.line_no, self.message, self.text)


class ParseResult:
    """Outcome of :func:`parse_bookmark_text`."""

    __slots__ = ("entries", "issues", "page_style_used", "total_lines")

    def __init__(self, entries, issues, page_style_used, total_lines):
        self.entries = entries
        self.issues = issues
        self.page_style_used = page_style_used
        self.total_lines = total_lines

    @property
    def without_page(self) -> int:
        return sum(1 for e in self.entries if e[2] is None)

    def __len__(self):
        return len(self.entries)


def roman_to_int(text: str) -> int | None:
    """Convert a Roman numeral to an int, or ``None`` if it is not one."""
    text = text.strip().upper()
    if not text or any(ch not in _ROMAN_VALUES for ch in text):
        return None
    total = 0
    prev = 0
    for ch in reversed(text):
        value = _ROMAN_VALUES[ch]
        if value < prev:
            total -= value
        else:
            total += value
            prev = value
    return total or None


def _is_roman(token: str) -> bool:
    return roman_to_int(token) is not None


def _split_page(text: str, page_style: str = "auto", bare_space: bool = True):
    """Split ``text`` into ``(title, page_token)``.

    With ``bare_space=False`` a plain space is not accepted as the separator
    before a page number, which keeps ``# Chapter 1`` intact.
    """
    text = text.rstrip()
    if not text:
        return text, None
    if page_style == "none":
        return text.strip(), None

    for regex in _EXPLICIT_PAGE_RES:
        m = regex.match(text)
        if m and m.group("title").strip():
            return m.group("title").strip(), m.group("page")

    decimal_re = _PAGE_TAIL_RE if bare_space else _PAGE_TAIL_STRICT_RE
    m = decimal_re.match(text)
    if m and m.group("title").strip():
        return m.group("title").strip(), m.group("page")

    if page_style in ("auto", "roman"):
        roman_re = _ROMAN_TAIL_RE if bare_space else _ROMAN_TAIL_STRICT_RE
        m = roman_re.match(text)
        if m and m.group("title").strip() and _is_roman(m.group("page")):
            return m.group("title").strip(), m.group("page")

    return text.strip(), None


def _chinese_depth(text: str):
    m = _CN_CHAPTER_RE.match(text)
    if m:
        kind = m.group(2)
        return (1 if kind == "节" else 0), text
    m = _CN_ITEM_RE.match(text)
    if m:
        return 1, (m.group(2) or text).strip()
    m = _CN_PLAIN_RE.match(text)
    if m:
        return 0, (m.group(2) or text).strip()
    return None, text


class _IndentTracker:
    """Map a run of leading whitespace onto a nesting level."""

    def __init__(self, tab_width: int = 4):
        self.tab_width = max(1, tab_width)
        self.levels = [0]

    def width(self, prefix: str) -> int:
        return len(prefix.expandtabs(self.tab_width))

    def depth(self, width: int) -> int:
        while len(self.levels) > 1 and width < self.levels[-1]:
            self.levels.pop()
        if width > self.levels[-1]:
            self.levels.append(width)
        return len(self.levels) - 1


def parse_bookmark_text(
    text: str,
    *,
    page_style: str = "auto",
    tab_width: int = 4,
    use_numbering: bool = True,
    use_csv: bool = True,
) -> ParseResult:
    """Parse bookmark text.

    ``page_style`` is one of ``auto``, ``decimal``, ``roman``, ``none``.
    """
    entries: list[tuple[int, str, str | None]] = []
    issues: list[ParseIssue] = []

    raw_lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    tracker = _IndentTracker(tab_width)

    # Work out whether this file is Markdown-heading driven.
    has_headings = any(_MD_HEADING_RE.match(line) for line in raw_lines)

    # Indentation is only meaningful when the file actually uses it.
    indents = []
    for line in raw_lines:
        if not line.strip() or _is_comment(line):
            continue
        m = _MD_HEADING_RE.match(line)
        if m:
            continue
        prefix_len = len(line) - len(line.lstrip())
        indents.append(prefix_len)
    uses_indent = any(i > 0 for i in indents)

    heading_depth = 0
    saw_numbering = False
    total_seen = 0

    for line_no, raw in enumerate(raw_lines, 1):
        stripped = raw.strip()
        if not stripped:
            continue
        if _is_comment(raw):
            continue

        total_seen += 1
        page_style_used = page_style

        # -- Markdown heading -------------------------------------------------
        m = _MD_HEADING_RE.match(raw)
        if m:
            depth = len(m.group(2)) - 1
            heading_depth = depth
            title, page = _split_page(m.group(3), page_style, bare_space=False)
            if not title:
                issues.append(ParseIssue(line_no, raw, "empty heading skipped"))
                continue
            entries.append((depth, title, page))
            tracker.levels = [0]
            continue

        # -- Markdown / plain list item --------------------------------------
        m = _MD_LIST_RE.match(raw)
        if m:
            indent = tracker.width(m.group(1))
            # A list under a heading sits one level below that heading.
            depth = tracker.depth(indent) + (heading_depth + 1 if has_headings else 0)
            title, page = _split_page(m.group(2), page_style)
            if not title:
                issues.append(ParseIssue(line_no, raw, "empty list item skipped"))
                continue
            entries.append((depth, title, page))
            continue

        # -- plain line --------------------------------------------------------
        prefix = raw[: len(raw) - len(raw.lstrip())]
        content = raw.strip()

        if use_csv:
            mc = _CSV_RE.match(content)
            if mc and "," not in mc.group("title"):
                title, page = mc.group("title").strip(), mc.group("page")
                depth = tracker.depth(tracker.width(prefix))
                entries.append((depth, title, page))
                continue

        body, page = _split_page(content, page_style)
        if not body:
            issues.append(ParseIssue(line_no, raw, "empty title skipped"))
            continue

        if uses_indent:
            depth = tracker.depth(tracker.width(prefix))
        else:
            # No indentation anywhere: fall back to numbering / Chinese markers.
            depth = None
            cn_depth, cn_body = _chinese_depth(body)
            if cn_depth is not None:
                depth = cn_depth
                body = cn_body or body
                saw_numbering = True
            elif use_numbering:
                mn = _NUMERIC_RE.match(body)
                if mn:
                    parts = re.split(r"[.．]", mn.group(1))
                    if len(parts) > 1 or mn.group(1).isdigit():
                        depth = max(0, len(parts) - 1)
                        saw_numbering = True
            if depth is None:
                depth = 0

        entries.append((depth, body, page))

    # Normalise so that the outermost level is always 0.  Without this, a file
    # whose every line is indented (a very common way to paste an outline) or
    # one that starts at "##" would nest every bookmark under the first one.
    if entries:
        base_depth = min(entry[0] for entry in entries)
        if base_depth:
            entries = [
                (depth - base_depth, title, page) for depth, title, page in entries
            ]

    if page_style == "auto":
        roman_hits = sum(
            1 for _d, _t, p in entries if p and not p.isdigit() and _is_roman(p)
        )
        numeric_hits = sum(1 for _d, _t, p in entries if p and p.isdigit())
        page_style_used = "roman" if roman_hits > numeric_hits else "decimal"
    else:
        page_style_used = page_style

    if entries and sum(1 for e in entries if e[2] is not None) == 0:
        issues.append(
            ParseIssue(
                0,
                "",
                "no page numbers were detected; every bookmark will point at the "
                "first page (use a line ending like 'Title 12')",
            )
        )

    return ParseResult(entries, issues, page_style_used, total_seen)


def _is_comment(line: str) -> bool:
    return any(regex.match(line) for regex in _COMMENT_RES)
