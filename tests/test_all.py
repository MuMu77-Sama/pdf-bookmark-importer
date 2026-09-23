#!/usr/bin/env python3
"""Test-suite for pdfbookmarks.

Run with::

    python tests/test_all.py

Every check is a real assertion over generated sample PDFs covering the three
xref layouts (classic table, xref stream, object stream) plus a damaged-xref
rebuild path.  A deliberately independent verifier re-checks the produced bytes
rather than trusting the library's own reader.
"""

from __future__ import annotations

import collections
import os
import re
import sys
import unittest
import uuid
import zlib
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for path in (ROOT, os.path.join(ROOT, "tools")):
    if path not in sys.path:
        sys.path.insert(0, path)

import make_sample_pdf  # noqa: E402
from pdfbookmarks import Bookmark, PDFDocument, add_outline, flatten_to_tree  # noqa: E402
from pdfbookmarks.pdfdoc import PDFError  # noqa: E402
from pdfbookmarks.pdfobj import decode_pdf_text_string  # noqa: E402
from pdfbookmarks.textparse import parse_bookmark_text  # noqa: E402

MODES = ("classic", "stream", "objstm")
PAGE_COUNT = 6


# ---------------------------------------------------------------------------
# Independent verification helpers (do not use PDFDocument for these)
# ---------------------------------------------------------------------------
def find_startxref(data: bytes) -> int:
    matches = list(re.finditer(rb"startxref\s+(\d+)", data))
    assert matches, "no startxref found"
    return int(matches[-1].group(1))


def assert_xref_offsets_are_real(test: unittest.TestCase, data: bytes) -> int:
    """Walk the newest xref section and check every offset points at its object.

    Returns the number of entries checked.  This is the check that catches the
    classic "wrote the wrong byte offset" bug.
    """
    offset = find_startxref(data)
    test.assertTrue(0 <= offset < len(data), "startxref out of range")
    probe = data[offset : offset + 64].lstrip(b"\x00\t\n\x0c\r ")
    checked = 0

    if probe.startswith(b"xref"):
        pos = data.index(b"xref", offset) + 4
        while True:
            m = re.compile(rb"\s*(\d+)\s+(\d+)\s*\n").match(data, pos)
            test.assertIsNotNone(m, "malformed xref subsection header")
            start, count = int(m.group(1)), int(m.group(2))
            if start == 0 and count == 0:
                break
            pos = m.end()
            for i in range(count):
                entry = re.compile(rb"(\d{10})\s+(\d{5})\s+([nf])\s*\n?").match(data, pos)
                test.assertIsNotNone(entry, "malformed xref entry at %d" % pos)
                num = start + i
                if entry.group(3) == b"n":
                    obj_off = int(entry.group(1))
                    header = re.compile(rb"\s*(\d+)\s+(\d+)\s+obj").match(
                        data, obj_off
                    )
                    test.assertIsNotNone(
                        header, "xref says object %d is at %d, nothing there" % (num, obj_off)
                    )
                    test.assertEqual(
                        int(header.group(1)),
                        num,
                        "xref points object %d at %d but found object %s"
                        % (num, obj_off, header.group(1).decode()),
                    )
                    checked += 1
                pos = entry.end()
            nxt = data[pos : pos + 16].lstrip(b"\x00\t\n\x0c\r ")
            if nxt.startswith(b"trailer"):
                break
        return checked

    # Cross-reference stream.
    header = re.compile(rb"\s*(\d+)\s+(\d+)\s+obj").match(data, offset)
    test.assertIsNotNone(header, "startxref does not point at an xref object")
    lex_pos = header.end()
    dict_start = data.index(b"<<", lex_pos)
    dict_end = data.index(b">>", dict_start) + 2
    head = data[dict_start:dict_end]
    width = re.search(rb"/W\s*\[\s*(\d+)\s+(\d+)\s+(\d+)\s*\]", head)
    test.assertIsNotNone(width, "xref stream has no /W")
    w = [int(width.group(i)) for i in (1, 2, 3)]
    size = int(re.search(rb"/Size\s+(\d+)", head).group(1))
    index = re.search(rb"/Index\s*\[([^\]]*)\]", head)
    if index:
        nums = [int(x) for x in index.group(1).split()]
    else:
        nums = [0, size]

    stream_at = data.index(b"stream", dict_end) + 6
    if data[stream_at : stream_at + 2] == b"\r\n":
        stream_at += 2
    elif data[stream_at : stream_at + 1] in (b"\n", b"\r"):
        stream_at += 1
    length = int(re.search(rb"/Length\s+(\d+)", head).group(1))
    payload = data[stream_at : stream_at + length]
    if b"/FlateDecode" in head:
        payload = zlib.decompress(payload)

    row = sum(w)
    cursor = 0
    for i in range(0, len(nums) - 1, 2):
        start, count = nums[i], nums[i + 1]
        for k in range(count):
            chunk = payload[cursor : cursor + row]
            cursor += row
            test.assertEqual(len(chunk), row, "truncated xref stream")
            f1 = int.from_bytes(chunk[: w[0]], "big") if w[0] else 1
            f2 = int.from_bytes(chunk[w[0] : w[0] + w[1]], "big") if w[1] else 0
            num = start + k
            if f1 == 1:
                obj_off = f2
                h = re.compile(rb"\s*(\d+)\s+(\d+)\s+obj").match(data, obj_off)
                test.assertIsNotNone(
                    h, "xref stream says object %d is at %d, nothing there" % (num, obj_off)
                )
                test.assertEqual(int(h.group(1)), num, "wrong object at offset %d" % obj_off)
                checked += 1
    return checked


def read_outline_tree(doc: PDFDocument):
    """Read the outline back as ``[(title, page_index, [children...])]``."""
    pages = doc.pages

    def page_index_of(dest) -> int | None:
        for candidate in (dest, doc.resolve(dest)):
            if isinstance(candidate, list) and candidate:
                first = candidate[0]
                if first in pages:
                    return pages.index(first)
            if isinstance(candidate, dict):
                d = candidate.get("D")
                if isinstance(d, list) and d and d[0] in pages:
                    return pages.index(d[0])
        return None

    def resolve_dest(item):
        if "Dest" in item:
            return page_index_of(item["Dest"])
        action = doc.dget(item, "A")
        if isinstance(action, dict) and "D" in action:
            return page_index_of(action["D"])
        return None

    def siblings(first):
        out = []
        ref = first
        guard = 0
        while ref is not None and guard < 5000:
            guard += 1
            item = doc.resolve(ref)
            if not isinstance(item, dict):
                break
            out.append(
                (
                    decode_pdf_text_string(doc.resolve(item.get("Title")) or b""),
                    resolve_dest(item),
                    siblings(item.get("First")),
                )
            )
            ref = item.get("Next")
        return out

    root = doc.dget(doc.root, "Outlines")
    if not isinstance(root, dict):
        return None
    return siblings(root.get("First"))


def flat(tree, depth=0):
    out = []
    for title, page, children in tree:
        out.append((depth, title, page))
        out.extend(flat(children, depth + 1))
    return out


class TempWorkspace:
    """Uniquely named scratch files created directly in the workspace root.

    ``tempfile.TemporaryDirectory`` cannot be used here: the sandbox allows
    writes into existing directories but not into newly created ones.
    """

    def __init__(self):
        self._paths = []

    def path(self, suffix="tmp"):
        full = os.path.join(ROOT, ".test-%s-%s" % (uuid.uuid4().hex[:10], suffix))
        self._paths.append(full)
        return full

    def cleanup(self):
        for path in self._paths:
            try:
                os.remove(path)
            except OSError:
                pass


def workspace_temp(case):
    temp = TempWorkspace()
    case.addCleanup(temp.cleanup)
    return temp


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------
class TestReading(unittest.TestCase):
    def test_all_modes_parse(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                data, _ = make_sample_pdf.build(PAGE_COUNT, mode=mode)
                doc = PDFDocument(data)
                self.assertEqual(doc.page_count(), PAGE_COUNT)
                self.assertFalse(doc.rebuilt)
                self.assertEqual(doc.root.get("Type"), "Catalog")
                self.assertGreater(assert_xref_offsets_are_real(self, data), 0)

    def test_object_stream_members_are_reachable(self):
        data, _ = make_sample_pdf.build(PAGE_COUNT, mode="objstm")
        doc = PDFDocument(data)
        compressed = [n for n, e in doc.xref.items() if e[0] == "c"]
        self.assertTrue(compressed, "expected compressed xref entries")
        for num in compressed:
            self.assertIsNotNone(doc.get_object(num), "object %d unreachable" % num)

    def test_page_order_matches_kids_order(self):
        data, page_nums = make_sample_pdf.build(PAGE_COUNT, mode="classic")
        doc = PDFDocument(data)
        refs = doc.pages
        self.assertEqual([r.num for r in refs], page_nums)

    def test_existing_outline_is_readable(self):
        for mode in ("classic", "objstm"):
            with self.subTest(mode=mode):
                data, _ = make_sample_pdf.build(PAGE_COUNT, mode=mode, with_outline=True)
                doc = PDFDocument(data)
                tree = read_outline_tree(doc)
                self.assertIsNotNone(tree)
                titles = [t for _d, t, _p in flat(tree)]
                self.assertEqual(titles, ["OLD bookmark one", "OLD bookmark two"])

    def test_damaged_xref_is_rebuilt(self):
        data, _ = make_sample_pdf.build(PAGE_COUNT, mode="classic")
        # Corrupt the startxref pointer, the way a truncated file would be.
        broken = re.sub(rb"startxref\s+\d+", b"startxref\n999999999", data)
        self.assertNotEqual(broken, data)
        doc = PDFDocument(broken)
        self.assertTrue(doc.rebuilt)
        self.assertEqual(doc.page_count(), PAGE_COUNT)

    def test_encrypted_flag(self):
        data, _ = make_sample_pdf.build(2, mode="classic")
        doc = PDFDocument(data)
        self.assertFalse(doc.is_encrypted)


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------
def nested_tree():
    return [
        Bookmark("Chapter 1", 0, [
            Bookmark("1.1 Background", 1),
            Bookmark("1.2 Contributions", 2, [
                Bookmark("1.2.1 Deep", 2),
            ]),
        ]),
        Bookmark("Chapter 2", 4),
    ]


class TestWriting(unittest.TestCase):
    def _roundtrip(self, mode, tree=None, **kwargs):
        data, _ = make_sample_pdf.build(PAGE_COUNT, mode=mode)
        doc = PDFDocument(data)
        tree = tree if tree is not None else nested_tree()
        out = add_outline(doc, tree, **kwargs)
        return data, out

    def test_roundtrip_all_modes(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                data, out = self._roundtrip(mode)
                self.assertTrue(
                    out.startswith(data.rstrip(b"\n")), "original bytes must be preserved"
                )
                assert_xref_offsets_are_real(self, out)
                doc2 = PDFDocument(out)
                self.assertFalse(doc2.rebuilt, "rewritten file must not need a rebuild")
                self.assertEqual(doc2.page_count(), PAGE_COUNT)
                tree = read_outline_tree(doc2)
                self.assertIsNotNone(tree, "no /Outlines in the output")
                entries = flat(tree)
                self.assertEqual(
                    entries,
                    [
                        (0, "Chapter 1", 0),
                        (1, "1.1 Background", 1),
                        (1, "1.2 Contributions", 2),
                        (2, "1.2.1 Deep", 2),
                        (0, "Chapter 2", 4),
                    ],
                )

    def test_outline_count_and_structure(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                _data, out = self._roundtrip(mode)
                doc = PDFDocument(out)
                outlines = doc.dget(doc.root, "Outlines")
                outlines_ref = doc.root.get("Outlines")
                self.assertEqual(outlines.get("Type"), "Outlines")
                self.assertEqual(outlines.get("Count"), 5)
                self.assertEqual(doc.dget(doc.root, "PageMode"), "UseOutlines")

                chapter1_ref = outlines.get("First")
                chapter1 = doc.resolve(chapter1_ref)
                self.assertEqual(
                    decode_pdf_text_string(doc.resolve(chapter1["Title"])), "Chapter 1"
                )
                self.assertEqual(chapter1.get("Count"), 3)
                self.assertIsNone(chapter1.get("Prev"))
                self.assertIsNotNone(chapter1.get("Next"))
                self.assertEqual(chapter1.get("Parent"), outlines_ref)

                child = doc.resolve(chapter1.get("First"))
                self.assertEqual(
                    decode_pdf_text_string(doc.resolve(child["Title"])), "1.1 Background"
                )
                self.assertEqual(child.get("Parent"), chapter1_ref)

                grand = doc.resolve(child.get("Next"))
                self.assertEqual(
                    decode_pdf_text_string(doc.resolve(grand["Title"])),
                    "1.2 Contributions",
                )
                self.assertEqual(grand.get("Count"), 1)
                self.assertEqual(grand.get("Parent"), chapter1_ref)

    def test_collapsed_outline_uses_negative_counts(self):
        _data, out = self._roundtrip("classic", expanded=False)
        doc = PDFDocument(out)
        outlines = doc.dget(doc.root, "Outlines")
        self.assertEqual(outlines.get("Count"), -5)

    def test_prev_next_chain_is_symmetric(self):
        _data, out = self._roundtrip("objstm")
        doc = PDFDocument(out)
        outlines = doc.dget(doc.root, "Outlines")
        outlines_ref = doc.root.get("Outlines")
        refs = _refs_of(doc, outlines)
        self.assertGreater(len(refs), 1)
        for i, ref in enumerate(refs):
            node = doc.resolve(ref)
            if i > 0:
                self.assertEqual(node.get("Prev"), refs[i - 1], "broken /Prev link")
            else:
                self.assertIsNone(node.get("Prev"))
            if i < len(refs) - 1:
                self.assertEqual(node.get("Next"), refs[i + 1], "broken /Next link")
            else:
                self.assertIsNone(node.get("Next"))
            self.assertEqual(node.get("Parent"), outlines_ref)

    def test_unicode_titles_roundtrip(self):
        tree = [
            Bookmark("第一章 绪论", 0),
            Bookmark("1.1 研究背景与意义", 1),
            Bookmark("Résumé & «quotes»", 2),
            Bookmark("Emoji \U0001f4d8 test", 3),
            Bookmark("Plain ASCII", 4),
        ]
        for mode in MODES:
            with self.subTest(mode=mode):
                _data, out = self._roundtrip(mode, tree=tree)
                doc = PDFDocument(out)
                entries = flat(read_outline_tree(doc))
                self.assertEqual(
                    [t for _d, t, _p in entries], [b.title for b in tree]
                )
                self.assertEqual([p for _d, _t, p in entries], [0, 1, 2, 3, 4])

    def test_page_less_bookmark_inherits_first_child(self):
        tree = [
            Bookmark("Part I", None, [Bookmark("Chapter 1", 3)]),
            Bookmark("Appendix", None),
        ]
        _data, out = self._roundtrip("classic", tree=tree)
        doc = PDFDocument(out)
        entries = flat(read_outline_tree(doc))
        self.assertEqual(entries[0][2], 3, "parent must inherit its first child target")

    def test_default_page_fills_missing_targets(self):
        tree = flatten_to_tree(
            [(0, "A", None), (1, "B", None)], default_page=2
        )
        _data, out = self._roundtrip("classic", tree=tree)
        doc = PDFDocument(out)
        entries = flat(read_outline_tree(doc))
        self.assertEqual([p for _d, _t, p in entries], [2, 2])

    def test_out_of_range_page_is_rejected(self):
        doc = PDFDocument(make_sample_pdf.build(PAGE_COUNT, mode="classic")[0])
        with self.assertRaises(PDFError):
            add_outline(doc, [Bookmark("Nope", 99)])

    def test_double_import_replaces_previous_outline(self):
        data, _ = make_sample_pdf.build(PAGE_COUNT, mode="classic")
        doc = PDFDocument(data)
        out = add_outline(doc, [Bookmark("First pass", 0)])
        doc2 = PDFDocument(out)
        out2 = add_outline(doc2, [Bookmark("Second pass", 1)])
        doc3 = PDFDocument(out2)
        self.assertFalse(doc3.rebuilt)
        assert_xref_offsets_are_real(self, out2)
        entries = flat(read_outline_tree(doc3))
        self.assertEqual([t for _d, t, _p in entries], ["Second pass"])

    def test_rebuilt_file_gets_a_self_contained_index(self):
        data, _ = make_sample_pdf.build(PAGE_COUNT, mode="classic")
        broken = re.sub(rb"startxref\s+\d+", b"startxref\n999999999", data)
        doc = PDFDocument(broken)
        self.assertTrue(doc.rebuilt)
        out = add_outline(doc, [Bookmark("Recovered", 2)])
        # The appended index has no /Prev, so it must list every object.
        tail = out[len(broken) :]
        self.assertNotIn(b"/Prev", tail)
        doc2 = PDFDocument(out)
        self.assertFalse(doc2.rebuilt)
        self.assertEqual(doc2.page_count(), PAGE_COUNT)
        self.assertEqual([t for _d, t, _p in flat(read_outline_tree(doc2))], ["Recovered"])

    def test_large_outline(self):
        tree = [
            Bookmark("Section %d" % i, i % PAGE_COUNT, [
                Bookmark("Sub %d.%d" % (i, j), (i + j) % PAGE_COUNT)
                for j in range(5)
            ])
            for i in range(60)
        ]
        for mode in MODES:
            with self.subTest(mode=mode):
                _data, out = self._roundtrip(mode, tree=tree)
                assert_xref_offsets_are_real(self, out)
                doc = PDFDocument(out)
                entries = flat(read_outline_tree(doc))
                self.assertEqual(len(entries), 60 * 6)
                self.assertEqual(doc.dget(doc.root, "Outlines").get("Count"), 360)


def _refs_of(doc, outlines):
    refs = []
    ref = outlines.get("First")
    while ref is not None:
        refs.append(ref)
        ref = doc.resolve(ref).get("Next")
    return refs


# ---------------------------------------------------------------------------
# Text parsing
# ---------------------------------------------------------------------------
class TestTextParsing(unittest.TestCase):
    def parse(self, text, **kwargs):
        return parse_bookmark_text(text, **kwargs).entries

    def test_indent_outline(self):
        entries = self.parse(
            "Chapter 1 1\n"
            "  Section 1.1 2\n"
            "    Deep 2\n"
            "  Section 1.2 3\n"
            "Chapter 2 4\n"
        )
        self.assertEqual(
            entries,
            [
                (0, "Chapter 1", "1"),
                (1, "Section 1.1", "2"),
                (2, "Deep", "2"),
                (1, "Section 1.2", "3"),
                (0, "Chapter 2", "4"),
            ],
        )

    def test_tab_indent(self):
        entries = self.parse("A 1\n\tB 2\n\t\tC 3\n")
        self.assertEqual([e[0] for e in entries], [0, 1, 2])

    def test_markdown_headings_and_lists(self):
        entries = self.parse(
            "# Chapter 1\n"
            "## Section 1.1\n"
            "- Point one\n"
            "  - Point two\n"
            "# Chapter 2\n"
        )
        self.assertEqual(
            entries,
            [
                (0, "Chapter 1", None),
                (1, "Section 1.1", None),
                (2, "Point one", None),
                (3, "Point two", None),
                (0, "Chapter 2", None),
            ],
        )

    def test_numbering_gives_depth_without_indent(self):
        entries = self.parse(
            "1 Introduction 1\n"
            "1.1 Background 2\n"
            "1.1.1 Detail 3\n"
            "2 Method 4\n"
        )
        self.assertEqual(
            entries,
            [
                (0, "1 Introduction", "1"),
                (1, "1.1 Background", "2"),
                (2, "1.1.1 Detail", "3"),
                (0, "2 Method", "4"),
            ],
        )

    def test_dot_leader_and_bar_and_dash(self):
        self.assertEqual(self.parse("Intro.........7"), [(0, "Intro", "7")])
        self.assertEqual(self.parse("Intro | 7"), [(0, "Intro", "7")])
        self.assertEqual(self.parse("Intro - 7"), [(0, "Intro", "7")])
        self.assertEqual(self.parse("Intro\t7"), [(0, "Intro", "7")])

    def test_number_glued_to_title_is_not_a_page(self):
        self.assertEqual(self.parse("方法2"), [(0, "方法2", None)])
        self.assertEqual(self.parse("Chapter 1"), [(0, "Chapter", "1")])

    def test_csv_and_comments(self):
        entries = self.parse("// a comment\nAlpha, 3\nBeta, 5\n")
        self.assertEqual(entries, [(0, "Alpha", "3"), (0, "Beta", "5")])

    def test_roman_page_numbers(self):
        entries = self.parse("Preface x\nIntroduction xi\n", page_style="roman")
        self.assertEqual(entries, [(0, "Preface", "x"), (0, "Introduction", "xi")])

    def test_auto_detects_roman(self):
        result = parse_bookmark_text("Preface iii\nBody iv\n")
        self.assertEqual(result.entries, [(0, "Preface", "iii"), (0, "Body", "iv")])

    def test_chinese_headings(self):
        entries = self.parse("第一章 绪论 1\n第二章 方法 5\n")
        self.assertEqual(
            entries, [(0, "第一章 绪论", "1"), (0, "第二章 方法", "5")]
        )

    def test_explicit_page_marker(self):
        self.assertEqual(self.parse("Preface 第 3 页"), [(0, "Preface", "3")])
        self.assertEqual(self.parse("Preface p3"), [(0, "Preface", "3")])

    def test_pages_none_gives_default(self):
        tree = flatten_to_tree(self.parse("A\nB\n"), default_page=0)
        self.assertEqual([b.page for b in tree], [0, 0])

    def test_sample_text_files_parse(self):
        expected = {
            "bookmarks_indent.txt": 11,
            "bookmarks_markdown.txt": 10,
            "bookmarks_numbered.txt": 9,
            "bookmarks_leader.txt": 4,
            "bookmarks_tab.txt": 5,
        }
        for name, count in expected.items():
            path = os.path.join(ROOT, "samples", name)
            if not os.path.exists(path):
                continue
            with self.subTest(sample=name):
                with open(path, encoding="utf-8") as handle:
                    result = parse_bookmark_text(handle.read())
                self.assertEqual(len(result.entries), count, "%s: %r" % (name, result.entries))
                if name == "bookmarks_markdown.txt":
                    # Markdown headings legitimately carry no page numbers.
                    self.assertTrue(all(e[2] is None for e in result.entries))
                else:
                    self.assertTrue(
                        all(e[2] is not None for e in result.entries),
                        "%s: %r" % (name, result.entries),
                    )


# ---------------------------------------------------------------------------
# End-to-end through the sample files on disk
# ---------------------------------------------------------------------------
class TestCjkWhitespace(unittest.TestCase):
    """Real-world CJK outlines lean on U+3000; it must behave like a space."""

    def parse(self, text, **kwargs):
        return parse_bookmark_text(text, **kwargs).entries

    def test_ideographic_space_before_page_number(self):
        self.assertEqual(self.parse("绪论\u300012"), [(0, "绪论", "12")])
        self.assertEqual(self.parse("绪论\u3000\u300012"), [(0, "绪论", "12")])

    def test_ideographic_space_as_numbering_separator(self):
        """The bug that flattened every chapter after the tenth."""
        entries = self.parse(
            "第10章\u3000用户数据报协议 335\n"
            "10.1\u3000引言 335\n"
            "10.2\u3000UDP头部 335\n"
            "10.2.1\u3000校验和 336\n"
        )
        self.assertEqual(
            entries,
            [
                (0, "第10章\u3000用户数据报协议", "335"),
                (1, "10.1\u3000引言", "335"),
                (1, "10.2\u3000UDP头部", "335"),
                (2, "10.2.1\u3000校验和", "336"),
            ],
        )

    def test_mixed_half_and_full_width_separators(self):
        """A file that switches separator style mid-way must still nest."""
        entries = self.parse(
            "1 绪论 1\n"
            "1.1 背景 1\n"
            "1.10\u3000参考文献 18\n"
            "2 方法 21\n"
            "2.1\u3000模型 21\n"
        )
        self.assertEqual([e[0] for e in entries], [0, 1, 1, 0, 1])

    def test_ideographic_space_indent(self):
        entries = self.parse(
            "\u3000Chapter 1 1\n"
            "\u3000\u3000Section 1.1 2\n"
            "\u3000Chapter 2 3\n"
        )
        self.assertEqual([e[0] for e in entries], [0, 1, 0])

    def test_uniform_indent_keeps_siblings(self):
        """Indenting every line must not chain them into one nested column."""
        entries = self.parse(
            "  Chapter 1 1\n  Chapter 2 2\n  Chapter 3 3\n"
        )
        self.assertEqual([e[0] for e in entries], [0, 0, 0])
        self.assertEqual([e[1] for e in entries], ["Chapter 1", "Chapter 2", "Chapter 3"])

    def test_uniform_indent_with_nesting(self):
        entries = self.parse(
            "  Chapter 1 1\n    Section 1.1 2\n  Chapter 2 3\n"
        )
        self.assertEqual([e[0] for e in entries], [0, 1, 0])

    def test_uniform_tab_indent(self):
        entries = self.parse("\tA 1\n\tB 2\n")
        self.assertEqual([e[0] for e in entries], [0, 0])

    def test_tree_shape_from_uniform_indent(self):
        """The same input must build a sibling forest, not a chain."""
        from pdfbookmarks import flatten_to_tree

        tree = flatten_to_tree(self.parse("  A 1\n  B 2\n  C 3\n"), default_page=0)
        self.assertEqual(len(tree), 3)
        self.assertTrue(all(not node.children for node in tree))

    def test_real_world_cjk_bookmark_file(self):
        path = os.path.join(ROOT, "samples", "bookmarks_test.txt")
        if not os.path.exists(path):
            self.skipTest("samples/bookmarks_test.txt not present")
        with open(path, encoding="utf-8-sig") as handle:
            result = parse_bookmark_text(handle.read())
        self.assertEqual(len(result.entries), 487)
        depths = collections.Counter(e[0] for e in result.entries)
        self.assertEqual(depths[0], 19, "expected exactly 19 top-level chapters: %r" % depths)
        titles = [t for _d, t, _p in result.entries]
        chapter10 = "第10章\u3000用户数据报协议和IP分片"
        self.assertIn(chapter10, titles)
        index = titles.index(chapter10)
        self.assertEqual(
            result.entries[index + 1][0], 1, "10.1 must nest under chapter 10"
        )


class TestEndToEnd(unittest.TestCase):
    def test_sample_files_exist_and_work(self):
        samples = os.path.join(ROOT, "samples")
        pdf = os.path.join(samples, "sample_objstm.pdf")
        txt = os.path.join(samples, "bookmarks_indent.txt")
        if not (os.path.exists(pdf) and os.path.exists(txt)):
            self.skipTest("samples not generated")
        with open(pdf, "rb") as handle:
            data = handle.read()
        with open(txt, encoding="utf-8") as handle:
            result = parse_bookmark_text(handle.read())
        doc = PDFDocument(data)
        tree = flatten_to_tree(result.entries, default_page=0)
        out = add_outline(doc, tree)
        doc2 = PDFDocument(out)
        entries = flat(read_outline_tree(doc2))
        self.assertEqual(len(entries), len(result.entries))
        self.assertEqual(entries[0][1], "第一章 绪论")
        self.assertEqual(entries[1][1], "1.1 研究背景")

    def test_real_world_700_page_pdf(self):
        """Regression test over the supplied 700-page PDF and its 487-line outline."""
        pdf = os.path.join(ROOT, "samples", "TestPdfPage__700.pdf")
        txt = os.path.join(ROOT, "samples", "bookmarks_test.txt")
        if not (os.path.exists(pdf) and os.path.exists(txt)):
            self.skipTest("real-world samples not present")
        from pdfbookmarks import cli

        temp = workspace_temp(self)
        out = temp.path("out700.pdf")
        report = cli.import_bookmarks(pdf, txt, out)
        self.assertEqual(report.bookmark_count, 487)
        self.assertEqual(report.top_level_count, 19)

        data = open(out, "rb").read()
        assert_xref_offsets_are_real(self, data)
        doc = PDFDocument(data)
        self.assertEqual(doc.page_count(), 700)
        self.assertFalse(doc.rebuilt)

        entries = flat(read_outline_tree(doc))
        self.assertEqual(len(entries), 487)
        titles = [t for _d, t, _p in entries]
        index = titles.index("第10章\u3000用户数据报协议和IP分片")
        self.assertEqual(entries[index][0], 0, "chapter 10 must be top level")
        self.assertEqual(entries[index + 1][0], 1, "10.1 must nest one level down")
        self.assertEqual(entries[index + 1][1], "10.1\u3000引言")
        self.assertEqual(entries[index + 1][2], 334, "10.1 targets page 335")


class TestGuiSmoke(unittest.TestCase):
    """The window must build and drive the shared import path end to end."""

    def setUp(self):
        try:
            import tkinter as tk
        except ImportError:  # pragma: no cover
            self.skipTest("tkinter is not available")
        try:
            self.root = tk.Tk()
        except Exception as exc:  # pragma: no cover - headless session
            self.skipTest("cannot open a display: %s" % exc)
        self.root.withdraw()
        from pdfbookmarks import gui

        self.gui = gui
        self.app = gui.BookmarkApp(self.root)
        self.root.update_idletasks()
        self.samples = os.path.join(ROOT, "samples")

    def tearDown(self):
        try:
            self.root.destroy()
        except Exception:
            pass

    def _patch_dialogs(self):
        return (
            mock.patch.object(self.gui.messagebox, "showwarning"),
            mock.patch.object(self.gui.messagebox, "showerror"),
            mock.patch.object(self.gui.messagebox, "showinfo"),
            mock.patch.object(self.gui.messagebox, "askyesno", return_value=True),
        )

    def test_preview_populates_tree(self):
        self.app.pdf_var.set(os.path.join(self.samples, "sample_classic.pdf"))
        self.app.text_var.set(os.path.join(self.samples, "bookmarks_indent.txt"))
        patches = self._patch_dialogs()
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.app.on_preview()
        self.assertEqual(len(self.app.tree.get_children()), 4)
        first = self.app.tree.get_children()[0]
        self.assertEqual(self.app.tree.item(first, "text"), "第一章 绪论")
        self.assertEqual(self.app.tree.item(first, "values")[0], "1")
        self.assertEqual(len(self.app.tree.get_children(first)), 2)

    def test_import_button_writes_a_valid_pdf(self):
        temp = workspace_temp(self)
        out = temp.path("gui_out.pdf")
        self.app.pdf_var.set(os.path.join(self.samples, "sample_xrefstream.pdf"))
        self.app.text_var.set(os.path.join(self.samples, "bookmarks_tab.txt"))
        self.app.out_var.set(out)
        patches = self._patch_dialogs()
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.app.on_import()
        self.assertTrue(os.path.exists(out), "GUI did not write the output")
        doc = PDFDocument(open(out, "rb").read())
        self.assertEqual(doc.page_count(), PAGE_COUNT)
        titles = [t for _d, t, _p in flat(read_outline_tree(doc))]
        self.assertEqual(
            titles, ["Introduction", "Background", "Method", "Results", "Conclusion"]
        )

    def test_paste_box_previews_and_imports(self):
        """The paste box must work with no file at all, alongside the picker."""
        temp = workspace_temp(self)
        out = temp.path("pasted.pdf")
        self.app.pdf_var.set(os.path.join(self.samples, "sample_classic.pdf"))
        self.app.source_var.set("paste")
        self.app._on_source_change()
        self.app.text_box.delete("1.0", "end")
        self.app.text_box.insert("1.0", "Chapter 1 1\n  1.1 Sub 2\nChapter 2 4\n")

        patches = self._patch_dialogs()
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

        self.app.on_preview()
        self.assertEqual(len(self.app.tree.get_children()), 2)

        self.app.out_var.set(out)
        self.app.on_import()
        self.assertTrue(os.path.exists(out), "paste box did not write the output")
        doc = PDFDocument(open(out, "rb").read())
        entries = flat(read_outline_tree(doc))
        self.assertEqual(
            entries,
            [(0, "Chapter 1", 0), (1, "1.1 Sub", 1), (0, "Chapter 2", 3)],
        )

    def test_paste_box_rejects_empty_text(self):
        self.app.pdf_var.set(os.path.join(self.samples, "sample_classic.pdf"))
        self.app.source_var.set("paste")
        self.app._on_source_change()
        self.app.text_box.delete("1.0", "end")
        with mock.patch.object(self.gui.messagebox, "showwarning") as warn:
            self.assertFalse(self.app._require_inputs())
            self.assertTrue(warn.called)

    def test_switching_source_toggles_widgets(self):
        self.app.source_var.set("file")
        self.app._on_source_change()
        self.assertEqual(str(self.app.text_entry.cget("state")), "normal")
        self.assertEqual(str(self.app.text_box.cget("state")), "disabled")
        self.app.source_var.set("paste")
        self.app._on_source_change()
        self.assertEqual(str(self.app.text_entry.cget("state")), "disabled")
        self.assertEqual(str(self.app.text_box.cget("state")), "normal")

    def test_list_button_reads_existing_outline(self):
        self.app.pdf_var.set(os.path.join(self.samples, "sample_with_outline.pdf"))
        patches = self._patch_dialogs()
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.app.on_list()
        self.assertEqual(len(self.app.tree.get_children()), 2)
        self.assertEqual(
            self.app.tree.item(self.app.tree.get_children()[0], "text"),
            "OLD bookmark one",
        )


class TestCli(unittest.TestCase):
    def test_import_and_list_roundtrip(self):
        from pdfbookmarks import cli

        temp = workspace_temp(self)
        out = temp.path("cli_out.pdf")
        code = cli.main(
            [
                os.path.join(ROOT, "samples", "sample_objstm.pdf"),
                os.path.join(ROOT, "samples", "bookmarks_leader.txt"),
                "-o",
                out,
                "--quiet",
            ]
        )
        self.assertEqual(code, 0)
        self.assertTrue(os.path.exists(out))

        import contextlib
        import io

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = cli.main([out, "--list"])
        self.assertEqual(code, 0)
        text = buffer.getvalue()
        self.assertIn("第一章 绪论", text)
        self.assertIn("p.1", text)

    def test_out_of_range_pages_fail_clearly(self):
        from pdfbookmarks import cli

        temp = workspace_temp(self)
        text = temp.path("b.txt")
        with open(text, "w", encoding="utf-8") as handle:
            handle.write("Way too far 999\n")
        with self.assertRaises(cli.ImportFailure) as ctx:
            cli.import_bookmarks(
                os.path.join(ROOT, "samples", "sample_classic.pdf"),
                text,
                temp.path("out.pdf"),
            )
        self.assertIn("outside the document", str(ctx.exception))

    def test_clamp_option_rescues_out_of_range(self):
        from pdfbookmarks import cli

        temp = workspace_temp(self)
        text = temp.path("b.txt")
        with open(text, "w", encoding="utf-8") as handle:
            handle.write("Far 999\n")
        out = temp.path("out.pdf")
        report = cli.import_bookmarks(
            os.path.join(ROOT, "samples", "sample_classic.pdf"),
            text,
            out,
            clamp=True,
        )
        self.assertEqual(report.bookmark_count, 1)
        doc = PDFDocument(open(out, "rb").read())
        entries = flat(read_outline_tree(doc))
        self.assertEqual(entries[0][2], PAGE_COUNT - 1)

    def test_same_input_and_output_is_rejected(self):
        from pdfbookmarks import cli

        same = os.path.join(ROOT, "samples", "sample_classic.pdf")
        with self.assertRaises(cli.ImportFailure):
            cli.import_bookmarks(same, same, same)


if __name__ == "__main__":
    unittest.main(verbosity=2)