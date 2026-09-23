"""PDF document reader: cross-reference tables/streams, object streams and the
page tree.  Pure standard library.

The reader is deliberately forgiving: it understands classic ``xref`` tables,
cross-reference *streams*, hybrid-reference files (``/XRefStm``), object
streams, and it can rebuild a damaged cross-reference table by scanning the
file for ``N G obj`` headers.
"""

from __future__ import annotations

import re

from .pdfobj import (
    Lexer,
    Name,
    PDFSyntaxError,
    Ref,
    Stream,
    decode_stream,
    parse_indirect_object,
)

__all__ = ["PDFError", "PDFDocument", "PAGE_LABEL_STYLES"]

_OBJ_SCAN_RE = re.compile(rb"(?<![0-9])(\d{1,10})\s+(\d{1,5})\s+obj\b")
_STARTXREF_RE = re.compile(rb"startxref\s+(\d+)")
_XREF_ENTRY_RE = re.compile(rb"(\d{1,10})\s+(\d{1,5})\s+([nf])")

PAGE_LABEL_STYLES = {
    "D": "decimal",
    "R": "upper-roman",
    "r": "lower-roman",
    "A": "upper-alpha",
    "a": "lower-alpha",
}


class PDFError(Exception):
    """Raised for structurally unusable PDF files."""


class PDFDocument:
    """A parsed PDF file.

    ``data`` is kept verbatim so that writing can be done as an incremental
    update (append-only), which never disturbs the original bytes.
    """

    def __init__(self, data: bytes):
        if not data:
            raise PDFError("file is empty")
        if not data.lstrip()[:5].startswith(b"%PDF-"):
            # A leading junk prefix is tolerated by readers; warn via attribute.
            self.has_header = False
        else:
            self.has_header = True
        if b"%%EOF" not in data[-1024:]:
            self.has_eof_marker = False
        else:
            self.has_eof_marker = True

        self.data = data
        self.xref: dict[int, tuple] = {}
        self.trailer: dict = {}
        self.rebuilt = False
        self.xref_offsets: list[int] = []
        self._cache: dict[int, object] = {}
        self._objstm_cache: dict[int, tuple] = {}
        self._loading: set[int] = set()
        self._pages: list | None = None
        self._root = None

        m = None
        for cand in _STARTXREF_RE.finditer(data):
            m = cand
        if m is not None:
            try:
                self._read_xref_chain(int(m.group(1)))
            except (PDFSyntaxError, PDFError, ValueError, IndexError):
                self.xref.clear()
                self.trailer.clear()

        if not self.xref or self.trailer.get("Root") is None:
            self._rebuild()

        if self.trailer.get("Root") is None:
            raise PDFError("could not locate the document catalogue (/Root)")

    # ------------------------------------------------------------------
    # Cross-reference parsing
    # ------------------------------------------------------------------
    def _read_xref_chain(self, offset: int) -> None:
        seen = set()
        while offset is not None and 0 <= offset < len(self.data) and offset not in seen:
            seen.add(offset)
            self.xref_offsets.append(offset)
            trailer = self._read_xref_section(offset)
            if trailer is None:
                break
            # Sections are visited newest-first, so first writer wins.
            for key, value in trailer.items():
                if key not in self.trailer:
                    self.trailer[key] = value

            xrefstm = trailer.get("XRefStm")
            if isinstance(xrefstm, int):
                try:
                    self._read_xref_stream(xrefstm, force=False)
                except Exception:
                    pass

            prev = trailer.get("Prev")
            offset = int(prev) if isinstance(prev, int) else None

    def _read_xref_section(self, offset: int):
        data = self.data
        lex = Lexer(data, offset)
        lex.skip_ws()
        if data.startswith(b"xref", lex.pos):
            lex.pos += 4
            return self._read_xref_table(lex)
        return self._read_xref_stream(lex.pos, force=True)

    def _read_xref_table(self, lex: Lexer):
        data = self.data
        while True:
            lex.skip_ws()
            if data.startswith(b"trailer", lex.pos):
                lex.pos += 7
                trailer = lex.parse_object()
                if not isinstance(trailer, dict):
                    raise PDFSyntaxError("trailer is not a dictionary")
                return trailer
            start = lex.parse_object()
            count = lex.parse_object()
            if not isinstance(start, int) or not isinstance(count, int):
                raise PDFSyntaxError("malformed xref subsection header")
            if count < 0 or count > 10_000_000:
                raise PDFSyntaxError("implausible xref subsection size")
            for i in range(count):
                lex.skip_ws()
                m = _XREF_ENTRY_RE.match(data, lex.pos)
                if not m:
                    raise PDFSyntaxError("malformed xref entry at %d" % lex.pos)
                lex.pos = m.end()
                num = start + i
                if m.group(3) == b"n" and num not in self.xref:
                    self.xref[num] = ("n", int(m.group(1)), int(m.group(2)))

    def _read_xref_stream(self, offset: int, force: bool):
        data = self.data
        try:
            _num, _gen, obj = parse_indirect_object(data, offset)
        except PDFSyntaxError:
            if force:
                raise
            return None
        if not isinstance(obj, Stream):
            if force:
                raise PDFSyntaxError("expected a cross-reference stream")
            return None

        d = obj.dict
        width = d.get("W")
        if not isinstance(width, list) or len(width) < 3:
            raise PDFSyntaxError("cross-reference stream lacks a /W array")
        w = [int(x) if isinstance(x, int) else 0 for x in width[:3]]
        row_len = sum(w)
        if row_len <= 0:
            raise PDFSyntaxError("cross-reference stream has a zero-width row")

        index = d.get("Index")
        if not isinstance(index, list) or len(index) < 2:
            size = d.get("Size")
            index = [0, int(size) if isinstance(size, int) else 0]

        payload = decode_stream(obj)
        pos = 0
        total = len(payload)
        for i in range(0, len(index) - 1, 2):
            try:
                start = int(index[i])
                count = int(index[i + 1])
            except (TypeError, ValueError):
                continue
            for k in range(count):
                if pos + row_len > total:
                    break
                chunk = payload[pos : pos + row_len]
                pos += row_len
                f1 = int.from_bytes(chunk[: w[0]], "big") if w[0] else 1
                f2 = int.from_bytes(chunk[w[0] : w[0] + w[1]], "big") if w[1] else 0
                f3 = (
                    int.from_bytes(chunk[w[0] + w[1] : w[0] + w[1] + w[2]], "big")
                    if w[2]
                    else 0
                )
                num = start + k
                if num in self.xref:
                    continue
                if f1 == 1:
                    self.xref[num] = ("n", f2, f3)
                elif f1 == 2:
                    self.xref[num] = ("c", f2, f3)
        return d

    def _rebuild(self) -> None:
        """Rebuild the xref by scanning for object headers (damaged-file path)."""
        self.rebuilt = True
        found: dict[int, int] = {}
        objstms: list[int] = []
        for m in _OBJ_SCAN_RE.finditer(self.data):
            num = int(m.group(1))
            if num == 0:
                continue
            # Later definitions win: this is what an incremental update means.
            found[num] = m.start()

        self.xref.clear()
        self.trailer.clear()
        for num, offset in found.items():
            self.xref[num] = ("n", offset, 0)

        # Locate a trailer dictionary, else synthesise one from an object stream
        # or from any object that looks like a catalogue.
        for m in re.finditer(rb"trailer", self.data):
            try:
                lex = Lexer(self.data, m.end())
                cand = lex.parse_object()
            except PDFSyntaxError:
                continue
            if isinstance(cand, dict) and cand.get("Root") is not None:
                self.trailer = dict(cand)

        # Register objects that live inside object streams, so that a rebuilt
        # index can reach them exactly like a normal compressed xref entry.
        for num in list(self.xref):
            try:
                _n, _g, obj = parse_indirect_object(self.data, self.xref[num][1])
            except Exception:
                continue
            if isinstance(obj, Stream) and obj.dict.get("Type") == "ObjStm":
                objstms.append(num)
        for num in objstms:
            try:
                pairs = self._objstm_pairs(num)
            except Exception:
                continue
            for idx, (onum, _off) in enumerate(pairs):
                self.xref.setdefault(onum, ("c", num, idx))

        candidates = []
        if self.trailer.get("Root") is not None:
            candidates.append(self.trailer["Root"])

        if not candidates:
            # Look for a /Type /Catalog anywhere we can now reach.
            for num in sorted(self.xref):
                try:
                    obj = self.get_object(num)
                except Exception:
                    continue
                if isinstance(obj, Stream):
                    obj = obj.dict
                if isinstance(obj, dict) and obj.get("Type") == "Catalog":
                    self.trailer["Root"] = Ref(num, 0)
                    candidates.append(Ref(num, 0))
                    break

        size = max(self.xref) + 1 if self.xref else 1
        self.trailer.setdefault("Size", size)

    # ------------------------------------------------------------------
    # Object access
    # ------------------------------------------------------------------
    def _objstm_pairs(self, stm_num: int) -> list:
        if stm_num in self._objstm_cache:
            return self._objstm_cache[stm_num][0]
        stm = self.get_object(stm_num)
        if not isinstance(stm, Stream):
            raise PDFError("object %d is not an object stream" % stm_num)
        n = self.resolve(stm.dict.get("N"))
        first = self.resolve(stm.dict.get("First"))
        if not isinstance(n, int) or not isinstance(first, int):
            raise PDFError("object stream %d has a bad /N or /First" % stm_num)
        payload = decode_stream(stm, self.resolve)
        header = payload[:first]
        lex = Lexer(header)
        pairs = []
        for _ in range(n):
            onum = lex.parse_object()
            off = lex.parse_object()
            if not isinstance(onum, int) or not isinstance(off, int):
                break
            pairs.append((onum, off))
        self._objstm_cache[stm_num] = (pairs, payload, first)
        return pairs

    def _objstm_object(self, stm_num: int, offset: int):
        pairs, payload, first = self._objstm_cache[stm_num]
        lex = Lexer(payload, first + offset)
        return lex.parse_object()

    def get_object(self, num: int):
        """Return the object with this number, or ``None``."""
        if num in self._cache:
            return self._cache[num]
        entry = self.xref.get(num)
        if entry is None:
            return None
        kind, a, b = entry
        if num in self._loading:
            return None
        self._loading.add(num)
        try:
            if kind == "n":
                if a < 0 or a >= len(self.data):
                    return None
                try:
                    _n, _g, obj = parse_indirect_object(self.data, a, resolve=self.resolve)
                except PDFSyntaxError:
                    return None
                self._cache[num] = obj
                return obj
            # Compressed object living inside an object stream.
            try:
                pairs = self._objstm_pairs(a)
            except Exception:
                return None
            if not (0 <= b < len(pairs)):
                return None
            try:
                obj = self._objstm_object(a, pairs[b][1])
            except Exception:
                return None
            self._cache[num] = obj
            return obj
        finally:
            self._loading.discard(num)

    def resolve(self, obj):
        """Follow indirect references until a direct object is reached."""
        hops = 0
        while isinstance(obj, Ref):
            obj = self.get_object(obj.num)
            hops += 1
            if hops > 64:
                return None
        return obj

    def dget(self, dictionary, key, default=None):
        """Resolve ``dictionary[key]`` one level."""
        if not isinstance(dictionary, dict):
            return default
        if key not in dictionary:
            return default
        value = self.resolve(dictionary[key])
        return default if value is None else value

    # ------------------------------------------------------------------
    # Document structure
    # ------------------------------------------------------------------
    @property
    def version(self) -> str:
        m = re.match(rb"%PDF-(\d\.\d)", self.data[:1024])
        return m.group(1).decode("ascii") if m else "1.4"

    @property
    def is_encrypted(self) -> bool:
        return self.trailer.get("Encrypt") is not None

    @property
    def root_ref(self) -> Ref | None:
        value = self.trailer.get("Root")
        if isinstance(value, Ref):
            return value
        return None

    @property
    def root(self):
        if self._root is None:
            self._root = self.resolve(self.trailer.get("Root")) or {}
        return self._root

    @property
    def pages(self) -> list:
        """Page object references, in reading order."""
        if self._pages is None:
            out: list = []
            self._walk_pages(self.root.get("Pages"), out, set(), 0)
            self._pages = out
        return self._pages

    def page_count(self) -> int:
        return len(self.pages)

    def _walk_pages(self, node, out: list, seen: set, depth: int) -> None:
        if depth > 128 or node is None:
            return
        key = node.num if isinstance(node, Ref) else id(node)
        if key in seen:
            return
        seen.add(key)

        obj = self.resolve(node)
        if isinstance(obj, Stream):
            obj = obj.dict
        if not isinstance(obj, dict):
            return

        kids = self.dget(obj, "Kids")
        if isinstance(kids, list) and kids and obj.get("Type") != "Page":
            for kid in kids:
                self._walk_pages(kid, out, seen, depth + 1)
        else:
            out.append(node)

    def page_ref(self, index):
        """Reference for a zero-based physical page index, or ``None``."""
        if not isinstance(index, int) or isinstance(index, bool):
            return None
        pages = self.pages
        if 0 <= index < len(pages):
            return pages[index]
        return None

    def get_page_labels(self) -> list:
        """Parse ``/PageLabels`` into ``[(start_index, style, prefix, start_num)]``."""
        labels = []
        root = self.root
        tree = self.dget(root, "PageLabels")
        if not isinstance(tree, dict):
            return labels
        nums = self.dget(tree, "Nums")
        if not isinstance(nums, list):
            return labels
        for i in range(0, len(nums) - 1, 2):
            idx = self.resolve(nums[i])
            spec = self.resolve(nums[i + 1])
            if not isinstance(idx, int) or not isinstance(spec, dict):
                continue
            style = spec.get("S")
            prefix = self.resolve(spec.get("P")) or b""
            start = self.resolve(spec.get("St"))
            labels.append(
                (
                    idx,
                    str(style) if isinstance(style, Name) else "D",
                    prefix if isinstance(prefix, bytes) else b"",
                    start if isinstance(start, int) else 1,
                )
            )
        labels.sort(key=lambda item: item[0])
        return labels

    def page_index_for_label(self, text: str) -> int | None:
        """Best-effort reverse lookup of a printed page label to a 0-based index."""
        text = text.strip()
        if not text:
            return None
        labels = self.get_page_labels()
        if not labels:
            return None
        pages = self.pages

        for i, (start, style, prefix, start_num) in enumerate(labels):
            end = labels[i + 1][0] if i + 1 < len(labels) else len(pages)
            pre = prefix.decode("latin-1")
            if not text.startswith(pre):
                continue
            body = text[len(pre) :]
            value = _label_to_number(body, style)
            if value is None:
                continue
            candidate = start + (value - start_num)
            if start <= candidate < end:
                return candidate
        return None


def _roman_to_int(text: str) -> int | None:
    values = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    total = 0
    prev = 0
    for ch in reversed(text.upper()):
        if ch not in values:
            return None
        value = values[ch]
        if value < prev:
            total -= value
        else:
            total += value
            prev = value
    return total or None


def _alpha_to_int(text: str) -> int | None:
    text = text.strip()
    if not text or not text.isalpha():
        return None
    total = 0
    for ch in text.upper():
        if not ("A" <= ch <= "Z"):
            return None
        total = total * 26 + (ord(ch) - ord("A") + 1)
    return total


def _label_to_number(body: str, style: str) -> int | None:
    body = body.strip()
    if style == "D":
        return int(body) if body.isdigit() else None
    if style in ("R", "r"):
        return _roman_to_int(body)
    if style in ("A", "a"):
        return _alpha_to_int(body)
    return int(body) if body.isdigit() else None
