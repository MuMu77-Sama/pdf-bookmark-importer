"""PDF object model and syntax parser.

Pure standard library.  Operates on raw ``bytes``; nothing is ever decoded to
text other than PDF *names* (which are a lossless latin-1 mapping).

Object mapping used throughout the package::

    /Name          -> Name(str)            (without the leading slash)
    123            -> int
    1.5            -> float
    (string)       -> bytes
    <hex>          -> bytes
    [ ... ]        -> list
    << ... >>      -> dict          (keys are Name, which is a str subclass)
    true / false   -> bool
    null           -> None
    12 0 R         -> Ref(12, 0)
    stream objects -> Stream
"""

from __future__ import annotations

import re
import zlib

__all__ = [
    "Name",
    "HexString",
    "Ref",
    "Stream",
    "PDFSyntaxError",
    "Lexer",
    "parse_indirect_object",
    "encode_pdf_text_string",
    "decode_pdf_text_string",
    "serialize_object",
    "decode_stream",
]

WHITESPACE = b"\x00\t\n\x0c\r "
DELIMITERS = b"()<>[]{}/%"
_EOL = b"\r\n"

_NUMBER_RE = re.compile(rb"[+-]?(?:\d+\.\d*|\.\d+|\d+)")
_OBJ_HEADER_RE = re.compile(rb"\s*(\d+)\s+(\d+)\s+obj\b")


class PDFSyntaxError(Exception):
    """Raised when the byte stream is not valid PDF syntax."""


class Name(str):
    """A PDF name object.  Stored without the leading slash."""

    __slots__ = ()

    def __repr__(self):  # pragma: no cover - debugging aid
        return "/" + str.__str__(self)


class HexString(bytes):
    """Raw string bytes that must be written as a ``<hex>`` string.

    Used for UTF-16BE text so that no binary byte ever appears in a literal
    string.
    """

    __slots__ = ()


class Ref:
    """An indirect reference such as ``12 0 R``."""

    __slots__ = ("num", "gen")

    def __init__(self, num: int, gen: int = 0):
        self.num = int(num)
        self.gen = int(gen)

    def __eq__(self, other):
        return isinstance(other, Ref) and (self.num, self.gen) == (other.num, other.gen)

    def __hash__(self):
        return hash((self.num, self.gen))

    def __repr__(self):  # pragma: no cover - debugging aid
        return "%d %d R" % (self.num, self.gen)


class Stream:
    """A PDF stream object: its dictionary plus the *raw* (still encoded) data."""

    __slots__ = ("dict", "raw", "_decoded")

    def __init__(self, dictionary: dict, raw: bytes):
        self.dict = dictionary
        self.raw = raw
        self._decoded = None

    def __repr__(self):  # pragma: no cover - debugging aid
        return "<Stream %d bytes %r>" % (len(self.raw), self.dict.get("Type"))


def _is_regular(data: bytes, pos: int) -> bool:
    """True when ``pos`` sits on a token boundary (EOF counts as a boundary)."""
    if pos >= len(data):
        return True
    return data[pos] in WHITESPACE or data[pos] in DELIMITERS


class Lexer:
    """A cursor over PDF bytes that can parse one object at a time."""

    __slots__ = ("data", "pos")

    def __init__(self, data: bytes, pos: int = 0):
        self.data = data
        self.pos = pos

    # -- low level ---------------------------------------------------------
    def skip_ws(self) -> None:
        data = self.data
        n = len(data)
        p = self.pos
        while p < n:
            c = data[p]
            if c in WHITESPACE:
                p += 1
            elif c == 0x25:  # '%' comment runs to end of line
                while p < n and data[p] not in b"\r\n":
                    p += 1
            else:
                break
        self.pos = p

    def at_keyword(self, keyword: bytes) -> bool:
        self.skip_ws()
        if not self.data.startswith(keyword, self.pos):
            return False
        return _is_regular(self.data, self.pos + len(keyword))

    def expect_keyword(self, keyword: bytes) -> None:
        if not self.at_keyword(keyword):
            raise PDFSyntaxError(
                "expected %r at offset %d" % (keyword, self.pos)
            )
        self.pos += len(keyword)

    # -- object grammar ----------------------------------------------------
    def parse_object(self):
        self.skip_ws()
        data = self.data
        p = self.pos
        if p >= len(data):
            raise PDFSyntaxError("unexpected end of data")

        c = data[p]
        if c == 0x2F:  # /
            return self.parse_name()
        if c == 0x28:  # (
            return self.parse_literal_string()
        if c == 0x3C:  # <
            if data[p : p + 2] == b"<<":
                return self.parse_dict()
            return self.parse_hex_string()
        if c == 0x5B:  # [
            return self.parse_array()
        if c in b"0123456789+-.":
            return self.parse_number_or_ref()

        for kw, value in ((b"true", True), (b"false", False), (b"null", None)):
            if data.startswith(kw, p) and _is_regular(data, p + len(kw)):
                self.pos = p + len(kw)
                return value

        raise PDFSyntaxError("cannot parse object at offset %d" % p)

    def parse_name(self) -> Name:
        data = self.data
        n = len(data)
        p = self.pos + 1
        out = bytearray()
        while p < n:
            c = data[p]
            if c in WHITESPACE or c in DELIMITERS:
                break
            if c == 0x23 and p + 2 < n:  # '#xx' escape
                try:
                    out.append(int(data[p + 1 : p + 3], 16))
                    p += 3
                    continue
                except ValueError:
                    pass
            out.append(c)
            p += 1
        self.pos = p
        return Name(out.decode("latin-1"))

    def parse_literal_string(self) -> bytes:
        data = self.data
        n = len(data)
        p = self.pos + 1
        depth = 1
        out = bytearray()
        while p < n:
            c = data[p]
            if c == 0x5C:  # backslash escape
                p += 1
                if p >= n:
                    break
                e = data[p]
                if e == 0x6E:
                    out.append(0x0A)
                    p += 1
                elif e == 0x72:
                    out.append(0x0D)
                    p += 1
                elif e == 0x74:
                    out.append(0x09)
                    p += 1
                elif e == 0x62:
                    out.append(0x08)
                    p += 1
                elif e == 0x66:
                    out.append(0x0C)
                    p += 1
                elif e in b"()\\":
                    out.append(e)
                    p += 1
                elif 0x30 <= e <= 0x37:  # 1-3 octal digits
                    start = p
                    while p < n and p - start < 3 and 0x30 <= data[p] <= 0x37:
                        p += 1
                    out.append(int(data[start:p], 8) & 0xFF)
                elif e == 0x0A:  # line continuation
                    p += 1
                elif e == 0x0D:
                    p += 1
                    if p < n and data[p] == 0x0A:
                        p += 1
                else:
                    out.append(e)
                    p += 1
            elif c == 0x28:  # nested (
                depth += 1
                out.append(c)
                p += 1
            elif c == 0x29:  # )
                depth -= 1
                if depth == 0:
                    p += 1
                    break
                out.append(c)
                p += 1
            elif c == 0x0D:  # EOL normalisation: CR / CRLF become LF
                out.append(0x0A)
                p += 1
                if p < n and data[p] == 0x0A:
                    p += 1
            else:
                out.append(c)
                p += 1
        self.pos = p
        return bytes(out)

    def parse_hex_string(self) -> bytes:
        data = self.data
        n = len(data)
        p = self.pos + 1
        digits = bytearray()
        while p < n and data[p] != 0x3E:  # '>'
            c = data[p]
            if c not in WHITESPACE:
                digits.append(c)
            p += 1
        self.pos = min(p + 1, n)
        if len(digits) % 2:
            digits.append(0x30)  # odd count is padded with a trailing zero
        try:
            return bytes.fromhex(digits.decode("ascii"))
        except ValueError:
            raise PDFSyntaxError("malformed hexadecimal string")

    def parse_array(self) -> list:
        self.pos += 1  # '['
        out = []
        while True:
            self.skip_ws()
            if self.pos >= len(self.data):
                raise PDFSyntaxError("unterminated array")
            if self.data[self.pos] == 0x5D:  # ']'
                self.pos += 1
                return out
            out.append(self.parse_object())

    def parse_dict(self) -> dict:
        self.pos += 2  # '<<'
        out = {}
        while True:
            self.skip_ws()
            if self.pos >= len(self.data):
                raise PDFSyntaxError("unterminated dictionary")
            if self.data[self.pos : self.pos + 2] == b">>":
                self.pos += 2
                return out
            key = self.parse_object()
            if not isinstance(key, Name):
                raise PDFSyntaxError("dictionary key is not a name")
            out[key] = self.parse_object()

    def parse_number_or_ref(self):
        data = self.data
        m = _NUMBER_RE.match(data, self.pos)
        if not m:
            raise PDFSyntaxError("bad number at offset %d" % self.pos)
        token = m.group()
        self.pos = m.end()

        if b"." not in token:
            # Could be the first half of "num gen R".
            save = self.pos
            self.skip_ws()
            m2 = _NUMBER_RE.match(data, self.pos)
            if m2 and b"." not in m2.group():
                after = m2.end()
                q = after
                n = len(data)
                while q < n and data[q] in WHITESPACE:
                    q += 1
                if data[q : q + 1] == b"R" and _is_regular(data, q + 1):
                    self.pos = q + 1
                    return Ref(int(token), int(m2.group()))
            self.pos = save
            return int(token)
        return float(token)


def parse_indirect_object(data: bytes, offset: int, resolve=None):
    """Parse ``N G obj ... endobj`` starting at ``offset``.

    ``resolve`` is an optional callable used to dereference an indirect
    ``/Length``.  Returns ``(objnum, gen, object)``.
    """
    m = _OBJ_HEADER_RE.match(data, offset)
    if not m:
        raise PDFSyntaxError("no 'obj' header at offset %d" % offset)
    objnum = int(m.group(1))
    gen = int(m.group(2))

    lex = Lexer(data, m.end())
    obj = lex.parse_object()

    lex.skip_ws()
    if isinstance(obj, dict) and data.startswith(b"stream", lex.pos) and _is_regular(
        data, lex.pos + 6
    ):
        p = lex.pos + 6
        if data[p : p + 2] == _EOL:
            p += 2
        elif p < len(data) and data[p] in b"\r\n":
            p += 1

        length = obj.get("Length")
        if isinstance(length, Ref):
            if resolve is None:
                length = None
            else:
                try:
                    length = resolve(length)
                except Exception:
                    length = None
        raw = None
        if isinstance(length, int) and length >= 0 and p + length <= len(data):
            candidate = data[p : p + length]
            # Trust /Length only when 'endstream' really follows it.
            tail = data[p + length : p + length + 20]
            if tail.lstrip(WHITESPACE).startswith(b"endstream"):
                raw = candidate
        if raw is None:
            end = data.find(b"endstream", p)
            if end < 0:
                raise PDFSyntaxError("unterminated stream in object %d" % objnum)
            raw = data[p:end]
            if raw.endswith(b"\r\n"):
                raw = raw[:-2]
            elif raw.endswith(b"\n") or raw.endswith(b"\r"):
                raw = raw[:-1]
        obj = Stream(obj, raw)

    return objnum, gen, obj


# ---------------------------------------------------------------------------
# Filter decoding
# ---------------------------------------------------------------------------
_PNG_PREDICTORS = (10, 11, 12, 13, 14, 15)


def _apply_predictor(data: bytes, params: dict) -> bytes:
    predictor = params.get("Predictor", 1)
    if not isinstance(predictor, int) or predictor < 2:
        return data
    colors = params.get("Colors", 1) or 1
    bpc = params.get("BitsPerComponent", 8) or 8
    columns = params.get("Columns", 1) or 1
    if not all(isinstance(v, int) for v in (colors, bpc, columns)):
        return data

    bpp = max(1, (colors * bpc + 7) // 8)
    row_len = (columns * colors * bpc + 7) // 8

    if predictor == 2:  # TIFF predictor
        if bpc != 8:
            return data
        out = bytearray(data)
        for r in range(0, len(out) - row_len + 1, row_len):
            for i in range(bpp, row_len):
                out[r + i] = (out[r + i] + out[r + i - bpp]) & 0xFF
        return bytes(out)

    if predictor not in _PNG_PREDICTORS:
        return data

    out = bytearray()
    prev = bytearray(row_len)
    pos = 0
    n = len(data)
    while pos + 1 <= n - 1:
        ft = data[pos]
        pos += 1
        row = bytearray(data[pos : pos + row_len])
        if len(row) < row_len:
            break
        pos += row_len
        if ft == 0:
            pass
        elif ft == 1:
            for i in range(bpp, row_len):
                row[i] = (row[i] + row[i - bpp]) & 0xFF
        elif ft == 2:
            for i in range(row_len):
                row[i] = (row[i] + prev[i]) & 0xFF
        elif ft == 3:
            for i in range(row_len):
                left = row[i - bpp] if i >= bpp else 0
                row[i] = (row[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif ft == 4:
            for i in range(row_len):
                a = row[i - bpp] if i >= bpp else 0
                b = prev[i]
                c = prev[i - bpp] if i >= bpp else 0
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                if pa <= pb and pa <= pc:
                    pr = a
                elif pb <= pc:
                    pr = b
                else:
                    pr = c
                row[i] = (row[i] + pr) & 0xFF
        else:
            raise PDFSyntaxError("unsupported PNG predictor %d" % ft)
        out.extend(row)
        prev = row
    return bytes(out)


def decode_stream(stream: Stream, resolve=None) -> bytes:
    """Decode a stream's data through the filters we support.

    Supports FlateDecode (with predictors), ASCIIHexDecode, ASCII85Decode and
    LZWDecode.  Image codecs (DCT/JPX/CCITT) are intentionally not supported;
    for those the raw bytes are returned.
    """
    if stream._decoded is not None:
        return stream._decoded

    raw = stream.raw
    filters = stream.dict.get("Filter")
    if filters is None:
        stream._decoded = raw
        return raw
    if not isinstance(filters, list):
        filters = [filters]

    parms = stream.dict.get("DecodeParms")
    if parms is None:
        parms = stream.dict.get("DP")
    if not isinstance(parms, list):
        parms = [parms] * len(filters)
    while len(parms) < len(filters):
        parms.append(None)

    data = raw
    for f, parm in zip(filters, parms):
        if isinstance(f, Ref) and resolve is not None:
            f = resolve(f)
        if isinstance(parm, Ref) and resolve is not None:
            parm = resolve(parm)
        if not isinstance(parm, dict):
            parm = {}

        if f == "FlateDecode" or f == "Fl":
            try:
                data = zlib.decompress(data)
            except zlib.error:
                # Tolerate truncated / trailing-garbage streams.
                d = zlib.decompressobj()
                try:
                    data = d.decompress(data)
                except zlib.error:
                    raise PDFSyntaxError("FlateDecode failed")
            data = _apply_predictor(data, parm)
        elif f == "ASCIIHexDecode" or f == "AHx":
            cleaned = bytes(c for c in data if c not in WHITESPACE)
            cleaned = cleaned.split(b">")[0]
            if len(cleaned) % 2:
                cleaned += b"0"
            data = bytes.fromhex(cleaned.decode("ascii"))
        elif f == "ASCII85Decode" or f == "A85":
            import base64

            body = data.strip()
            if body.startswith(b"<~"):
                body = body[2:]
            idx = body.find(b"~>")
            if idx >= 0:
                body = body[:idx]
            data = base64.a85decode(body, adobe=False)
        elif f == "LZWDecode" or f == "LZW":
            data = _lzw_decode(data, parm.get("EarlyChange", 1))
        else:
            # Cannot decode (image codec); hand back what we have.
            break

    stream._decoded = data
    return data


def _lzw_decode(data: bytes, early_change: int = 1) -> bytes:
    """PDF flavour LZW (MSB-first, 256 is a clear-table code)."""
    out = bytearray()
    table = [bytes([i]) for i in range(256)] + [b"", b""]
    code_len = 9
    prev = None
    bitbuf = 0
    nbits = 0
    early_change = 1 if early_change else 0

    for byte in data:
        bitbuf = (bitbuf << 8) | byte
        nbits += 8
        while nbits >= code_len:
            code = (bitbuf >> (nbits - code_len)) & ((1 << code_len) - 1)
            nbits -= code_len
            if code == 256:
                table = [bytes([i]) for i in range(256)] + [b"", b""]
                code_len = 9
                prev = None
                continue
            if code == 257:
                return bytes(out)
            if prev is None:
                entry = table[code]
            elif code < len(table):
                entry = table[code]
                table.append(prev + entry[:1])
            else:
                entry = prev + prev[:1]
                table.append(entry)
            out.extend(entry)
            prev = entry
            if len(table) + early_change >= (1 << code_len) and code_len < 12:
                code_len += 1
    return bytes(out)


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------
def encode_pdf_text_string(text: str):
    """Encode ``text`` as a serialisable PDF string object.

    Plain printable ASCII becomes a literal ``(string)``; anything else becomes
    a UTF-16BE ``<hex>`` string with a BOM, which is the portable way to carry
    CJK titles through a PDF outline.
    """
    if all(0x20 <= ord(ch) <= 0x7E for ch in text):
        return text.encode("ascii")
    return HexString(b"\xfe\xff" + text.encode("utf-16-be"))


def _fmt_number(value) -> bytes:
    if isinstance(value, int):
        return b"%d" % value
    if value == int(value):
        return b"%d" % int(value)
    return ("%.6f" % value).rstrip("0").rstrip(".").encode("ascii")


def serialize_object(obj) -> bytes:
    """Serialise a parsed object back to PDF syntax."""
    if obj is None:
        return b"null"
    if obj is True:
        return b"true"
    if obj is False:
        return b"false"
    if isinstance(obj, Name):
        raw = str(obj).encode("latin-1")
        out = bytearray(b"/")
        for b in raw:
            if b in WHITESPACE or b in DELIMITERS or b < 0x21 or b > 0x7E:
                out.extend(b"#%02X" % b)
            else:
                out.append(b)
        return bytes(out)
    if isinstance(obj, Ref):
        return b"%d %d R" % (obj.num, obj.gen)
    if isinstance(obj, (int, float)):
        return _fmt_number(obj)
    if isinstance(obj, HexString):
        return b"<" + bytes(obj).hex().upper().encode("ascii") + b">"
    if isinstance(obj, bytes):
        return encode_pdf_text_string_bytes(obj)
    if isinstance(obj, str):
        # encode_pdf_text_string yields the *content* (bytes or HexString), so
        # it still needs to be wrapped in string delimiters.
        return serialize_object(encode_pdf_text_string(obj))
    if isinstance(obj, (list, tuple)):
        return b"[" + b" ".join(serialize_object(v) for v in obj) + b"]"
    if isinstance(obj, dict):
        parts = [b"<<"]
        for key, value in obj.items():
            parts.append(serialize_object(Name(key) if isinstance(key, str) else key))
            parts.append(serialize_object(value))
        parts.append(b">>")
        return b" ".join(parts)
    if isinstance(obj, Stream):
        body = serialize_object(obj.dict)
        return body + b"\nstream\n" + obj.raw + b"\nendstream"
    raise PDFSyntaxError("cannot serialise %r" % (obj,))


def decode_pdf_text_string(raw: bytes) -> str:
    """Decode PDF string bytes into text.

    Handles the UTF-16BE/LE BOM forms; anything else falls back to
    PDFDocEncoding approximated by latin-1 (correct for the ASCII range and for
    the accented positions most documents actually use).
    """
    if raw.startswith(b"\xfe\xff"):
        return raw[2:].decode("utf-16-be", "replace")
    if raw.startswith(b"\xff\xfe"):
        return raw[2:].decode("utf-16-le", "replace")
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8", "replace")
    return raw.decode("latin-1")


def encode_pdf_text_string_bytes(data: bytes) -> bytes:
    """Serialise raw string bytes (already PDF-encoded) as a literal string."""
    body = (
        data.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")
    )
    return b"(" + body + b")"
