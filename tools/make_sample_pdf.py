#!/usr/bin/env python3
"""Generate the sample PDFs used by the test-suite and for manual demos.

Standalone on purpose: it must not import ``pdfbookmarks`` so that the samples
stay an independent cross-check of the reader/writer.

Modes
-----
classic   object streams/xref table, the most common layout
stream    cross-reference *stream* with all objects uncompressed
objstm    cross-reference stream plus a real object stream
"""

from __future__ import annotations

import os
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLES = os.path.join(os.path.dirname(HERE), "samples")

FONT_NUM = 3


def esc(text: str) -> bytes:
    return (
        text.encode("latin-1", "replace")
        .replace(b"\\", b"\\\\")
        .replace(b"(", b"\\(")
        .replace(b")", b"\\)")
    )


def page_content(number: int, total: int, label: str = "") -> bytes:
    lines = [
        b"BT /F1 48 Tf 72 700 Td (Page %d) Tj ET" % number,
        b"BT /F1 14 Tf 72 640 Td (of %d) Tj ET" % total,
        b"BT /F1 12 Tf 72 600 Td (bookmark target marker: P%d) Tj ET" % number,
    ]
    if label:
        lines.append(b"BT /F1 12 Tf 72 570 Td (" + esc(label) + b") Tj ET")
    # A distinctive bar so a human can see at a glance which page is shown.
    height = 8 + number * 12
    lines.append(b"0 0 1 rg 72 400 %d %d re f" % (120 + number * 20, height))
    return b"\n".join(lines)


def build(page_count: int = 6, mode: str = "classic", with_outline: bool = False):
    """Return ``(pdf_bytes, page_object_numbers)``."""
    objects: dict[int, bytes] = {}
    stream_objects: set[int] = set()

    content_nums = []
    page_nums = []
    num = 4
    for i in range(page_count):
        page_nums.append(num)
        content_nums.append(num + 1)
        num += 2

    objects[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    kids = b" ".join(b"%d 0 R" % n for n in page_nums)
    objects[2] = (
        b"<< /Type /Pages /Count %d /Kids [%s] >>" % (page_count, kids)
    )
    objects[FONT_NUM] = (
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
    )

    for i in range(page_count):
        data = page_content(i + 1, page_count, "chapter %d" % (i + 1))
        objects[content_nums[i]] = (
            b"<< /Length %d >>\nstream\n" % len(data) + data + b"\nendstream"
        )
        stream_objects.add(content_nums[i])
        objects[page_nums[i]] = (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            b"/Resources << /Font << /F1 %d 0 R >> >> /Contents %d 0 R >>"
            % (FONT_NUM, content_nums[i])
        )

    if with_outline:
        root_num, first_num, second_num = 100, 101, 102
        objects[1] = (
            b"<< /Type /Catalog /Pages 2 0 R /Outlines %d 0 R "
            b"/PageMode /UseOutlines >>" % root_num
        )
        objects[root_num] = (
            b"<< /Type /Outlines /First %d 0 R /Last %d 0 R /Count 2 >>"
            % (first_num, second_num)
        )
        objects[first_num] = (
            b"<< /Title (OLD bookmark one) /Parent %d 0 R /Next %d 0 R "
            b"/Dest [%d 0 R /XYZ null null null] >>"
            % (root_num, second_num, page_nums[0])
        )
        objects[second_num] = (
            b"<< /Title (OLD bookmark two) /Parent %d 0 R /Prev %d 0 R "
            b"/Dest [%d 0 R /XYZ null null null] >>"
            % (root_num, first_num, page_nums[min(2, page_count - 1)])
        )

    if mode == "classic":
        return _write_classic(objects), page_nums
    if mode == "stream":
        return _write_xref_stream(objects, stream_objects, use_objstm=False), page_nums
    if mode == "objstm":
        return _write_xref_stream(objects, stream_objects, use_objstm=True), page_nums
    raise ValueError("unknown mode %r" % mode)


def _header() -> bytes:
    return b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n"


def _write_classic(objects: dict[int, bytes]) -> bytes:
    out = bytearray(_header())
    offsets: dict[int, int] = {}
    for num in sorted(objects):
        offsets[num] = len(out)
        out += b"%d 0 obj\n" % num + objects[num] + b"\nendobj\n"
    xref_offset = len(out)
    highest = max(objects)
    out += b"xref\n0 %d\n" % (highest + 1)
    out += b"0000000000 65535 f \n"
    for num in range(1, highest + 1):
        off = offsets.get(num)
        if off is None:
            out += b"0000000000 65535 f \n"
        else:
            out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\n" % (highest + 1)
    out += b"startxref\n%d\n%%%%EOF\n" % xref_offset
    return bytes(out)


def _write_xref_stream(objects: dict[int, bytes], stream_objects: set, use_objstm: bool):
    out = bytearray(_header())
    offsets: dict[int, int] = {}
    compressed: dict[int, int] = {}  # objnum -> index inside the object stream

    highest = max(objects)
    objstm_num = highest + 1
    xref_num = highest + 2

    if use_objstm:
        packable = [n for n in sorted(objects) if n not in stream_objects]
        header = bytearray()
        body = bytearray()
        for index, objnum in enumerate(packable):
            header += b"%d %d " % (objnum, len(body))
            body += objects[objnum] + b"\n"
            compressed[objnum] = index
        payload = zlib.compress(bytes(header) + bytes(body), 9)
        dict_bytes = (
            b"<< /Type /ObjStm /N %d /First %d /Filter /FlateDecode /Length %d >>"
            % (len(packable), len(header), len(payload))
        )
        offsets[objstm_num] = len(out)
        out += (
            b"%d 0 obj\n" % objstm_num
            + dict_bytes
            + b"\nstream\n"
            + payload
            + b"\nendstream\nendobj\n"
        )

    # Uncompressed objects (only the content streams in this sample).
    for num in sorted(objects):
        if num in compressed:
            continue
        offsets[num] = len(out)
        out += b"%d 0 obj\n" % num + objects[num] + b"\nendobj\n"

    xref_offset = len(out)
    rows = []
    for num in range(0, xref_num + 1):
        if num == 0:
            rows.append((0, 0, 65535))
        elif num == xref_num:
            rows.append((1, xref_offset, 0))
        elif num in compressed:
            rows.append((2, objstm_num, compressed[num]))
        elif num in offsets:
            rows.append((1, offsets[num], 0))
        else:
            rows.append((0, 0, 65535))

    payload = bytearray()
    for kind, a, b in rows:
        payload += bytes([kind]) + a.to_bytes(4, "big") + b.to_bytes(2, "big")
    blob = zlib.compress(bytes(payload), 9)
    xref_dict = (
        b"<< /Type /XRef /Size %d /W [1 4 2] /Root 1 0 R "
        b"/Filter /FlateDecode /Length %d >>" % (xref_num + 1, len(blob))
    )
    out += (
        b"%d 0 obj\n" % xref_num
        + xref_dict
        + b"\nstream\n"
        + blob
        + b"\nendstream\nendobj\n"
    )
    out += b"startxref\n%d\n%%%%EOF\n" % xref_offset
    return bytes(out)


BOOKMARK_TEXTS = {
    "bookmarks_indent.txt": """\
第一章 绪论 1
  1.1 研究背景 1
  1.2 研究意义 2
第二章 相关工作 3
  2.1 国内研究 3
    2.1.1 早期工作 3
  2.2 国外研究 4
第三章 方法 5
  3.1 模型设计 5
  3.2 实验设置 6
第四章 结论 6
""",
    "bookmarks_markdown.txt": """\
# Chapter 1 Introduction
## 1.1 Background
- Motivation
  - Why it matters
- Problem statement
## 1.2 Contributions
# Chapter 2 Method
## 2.1 Model
## 2.2 Training
# Chapter 3 Results
""",
    "bookmarks_numbered.txt": """\
1 绪论 1
1.1 背景 1
1.2 意义 2
2 方法 3
2.1 模型 3
2.1.1 编码器 3
2.2 训练 4
3 实验 5
4 结论 6
""",
    "bookmarks_leader.txt": """\
第一章 绪论....................1
第二章 相关工作................3
第三章 方法....................5
第四章 结论....................6
""",
    "bookmarks_tab.txt": """\
Introduction\t1
Background\t2
Method\t3
Results\t5
Conclusion\t6
""",
}


def write_text_samples() -> list[str]:
    os.makedirs(SAMPLES, exist_ok=True)
    paths = []
    for name, content in BOOKMARK_TEXTS.items():
        path = os.path.join(SAMPLES, name)
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        paths.append(path)
    return paths


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    os.makedirs(SAMPLES, exist_ok=True)
    written = []
    for mode, name in (
        ("classic", "sample_classic.pdf"),
        ("stream", "sample_xrefstream.pdf"),
        ("objstm", "sample_objstm.pdf"),
        ("classic", "sample_with_outline.pdf"),
    ):
        with_outline = "with_outline" in name
        data, _pages = build(6, mode=mode, with_outline=with_outline)
        path = os.path.join(SAMPLES, name)
        with open(path, "wb") as handle:
            handle.write(data)
        written.append((path, len(data)))

    for mode, name in (
        ("objstm", "sample_objstm_with_outline.pdf"),
    ):
        data, _pages = build(6, mode=mode, with_outline=True)
        path = os.path.join(SAMPLES, name)
        with open(path, "wb") as handle:
            handle.write(data)
        written.append((path, len(data)))

    write_text_samples()
    for path, size in written:
        print("wrote %s (%d bytes)" % (path, size))
    print("wrote %d bookmark text samples" % len(BOOKMARK_TEXTS))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
