"""Write bookmark outlines into an existing PDF.

The write is an *incremental update*: the original bytes are kept untouched and
new objects plus a new cross-reference section are appended.  That means no
content stream, image or font is ever re-encoded, so the risk of damaging the
document is minimal, and the operation is fast even for large files.

Two append styles are supported, matching whatever the source file uses:

* classic ``xref`` table + ``trailer``
* cross-reference *stream* (``/Type /XRef``)
"""

from __future__ import annotations

import zlib

from .pdfobj import Name, Ref, serialize_object
from .pdfdoc import PDFDocument, PDFError

__all__ = ["Bookmark", "add_outline", "count_items", "flatten_to_tree"]

TRAILER_WHITELIST = ("Size", "Root", "Info", "ID", "Encrypt")


class Bookmark:
    """A node of the bookmark tree.

    ``page`` is a **zero-based physical page index** into ``doc.pages``; when it
    is ``None`` the bookmark inherits the target of its first descendant, or
    falls back to ``default_page``.
    """

    __slots__ = ("title", "page", "children")

    def __init__(self, title: str, page: int | None = None, children=None):
        self.title = str(title)
        self.page = page
        self.children = list(children) if children else []

    def __repr__(self):  # pragma: no cover - debugging aid
        return "Bookmark(%r, page=%r, children=%d)" % (
            self.title,
            self.page,
            len(self.children),
        )


def count_items(items) -> int:
    """Total number of nodes in the forest."""
    return sum(1 + count_items(item.children) for item in items)


def flatten_to_tree(entries, page_resolver=None, default_page=None):
    """Turn ``[(depth, title, page_label_or_index)]`` into a ``Bookmark`` forest.

    ``entries`` must be ordered as it should appear in the outline.  Depth is a
    zero-based nesting level.  ``page_resolver`` maps the raw page field to a
    zero-based page index (returning ``None`` when it cannot be resolved).
    """
    roots: list[Bookmark] = []
    # stack[d] is the last node seen at depth d
    stack: list[Bookmark] = []
    for entry in entries:
        depth, title, raw_page = entry[0], entry[1], entry[2]
        depth = max(0, int(depth))
        page = None
        if isinstance(raw_page, int):
            page = raw_page
        elif raw_page is not None and page_resolver is not None:
            page = page_resolver(raw_page)

        node = Bookmark(title, page)
        if depth == 0 or not stack:
            if depth > 0 and stack:
                # A jump deeper than one level: clamp to the deepest open node.
                stack[-1].children.append(node)
                del stack[len(stack) :]
                stack.append(node)
                continue
            roots.append(node)
            stack = [node]
        else:
            if depth >= len(stack):
                parent = stack[-1]
                stack.append(node)
            else:
                del stack[depth:]
                parent = stack[depth - 1]
                stack.append(node)
            parent.children.append(node)
    if default_page is not None:
        _apply_default_page(roots, default_page)
    return roots


def _apply_default_page(items, default_page: int) -> None:
    """Give page-less leaves a target by inheriting from the nearest descendant."""
    for item in items:
        _apply_default_page(item.children, default_page)
        if item.page is None:
            inherited = _first_page(item.children)
            item.page = inherited if inherited is not None else default_page


def _first_page(items):
    for item in items:
        if item.page is not None:
            return item.page
        found = _first_page(item.children)
        if found is not None:
            return found
    return None


# ---------------------------------------------------------------------------
# Incremental update construction
# ---------------------------------------------------------------------------
def add_outline(
    doc: PDFDocument,
    tree,
    *,
    expanded: bool = True,
    show_panel: bool = True,
) -> bytes:
    """Return the complete output PDF: original bytes plus the new outline.

    Writing an outline is always an append, so every original byte survives
    untouched; the returned value is simply ``source + appended section``.
    Any outline already present is replaced, because the new ``/Root`` points
    at the freshly written ``/Outlines`` tree.
    """
    if doc.is_encrypted:
        raise PDFError(
            "this PDF is encrypted; decrypt it first (the tool cannot rewrite "
            "an encrypted document)"
        )
    if not tree:
        raise PDFError("no bookmarks to write")

    page_refs = doc.pages
    if not page_refs:
        raise PDFError("the PDF has no pages, so bookmarks cannot be attached")

    # A bookmark without its own page inherits the first page found among its
    # descendants (so a "Part I" node still jumps somewhere useful).  The
    # effective values are kept in a side table rather than mutating the
    # caller's tree.
    resolved: dict[int, int | None] = {}
    _resolve_pages(tree, resolved)

    for item in _walk_items(tree):
        page = resolved[id(item)]
        if page is not None and not (0 <= page < len(page_refs)):
            raise PDFError(
                "bookmark %r targets page %d but the PDF only has %d pages"
                % (item.title, page + 1, len(page_refs))
            )

    source = doc.data
    prefix = source if source.endswith(b"\n") else source + b"\n"
    base_offset = len(prefix)

    trailer = doc.trailer
    size_hint = trailer.get("Size")
    if isinstance(size_hint, Ref):
        size_hint = doc.resolve(size_hint)
    base_num = max(
        (max(doc.xref) + 1) if doc.xref else 1,
        size_hint if isinstance(size_hint, int) else 0,
        1,
    )

    # ---- allocate object numbers (matches ascending emit order) ----------
    numbers: dict[int, int] = {}
    cursor = base_num
    outlines_num = cursor
    cursor += 1

    def assign(items):
        nonlocal cursor
        for item in items:
            numbers[id(item)] = cursor
            cursor += 1
            assign(item.children)

    assign(tree)
    new_root_num = cursor
    cursor += 1
    use_stream = _uses_xref_stream(doc) and not doc.rebuilt
    xref_num = cursor if use_stream else None
    if use_stream:
        cursor += 1
    new_size = cursor

    # ---- build object bodies --------------------------------------------
    bodies: list[tuple[int, bytes]] = []

    def emit_items(items, parent_num):
        """Depth-first, matching the object-number allocation order."""
        nums = [numbers[id(item)] for item in items]
        for index, item in enumerate(items):
            entry: dict = {"Title": item.title, "Parent": Ref(parent_num)}
            if index > 0:
                entry["Prev"] = Ref(nums[index - 1])
            if index < len(nums) - 1:
                entry["Next"] = Ref(nums[index + 1])
            page = doc.page_ref(resolved[id(item)])
            if page is not None and isinstance(page, Ref):
                entry["Dest"] = [page, Name("XYZ"), None, None, None]
            if item.children:
                child_nums = [numbers[id(c)] for c in item.children]
                total = count_items(item.children)
                entry["First"] = Ref(child_nums[0])
                entry["Last"] = Ref(child_nums[-1])
                entry["Count"] = total if expanded else -total
            bodies.append((numbers[id(item)], serialize_object(entry)))
            if item.children:
                emit_items(item.children, numbers[id(item)])

    emit_items(tree, outlines_num)

    total = count_items(tree)
    outlines_entry: dict = {"Type": Name("Outlines")}
    if tree:
        outlines_entry["First"] = Ref(numbers[id(tree[0])])
        outlines_entry["Last"] = Ref(numbers[id(tree[-1])])
        outlines_entry["Count"] = total if expanded else -total
    bodies.insert(0, (outlines_num, serialize_object(outlines_entry)))

    new_root = dict(doc.root) if isinstance(doc.root, dict) else {}
    new_root["Type"] = Name("Catalog")
    new_root["Outlines"] = Ref(outlines_num)
    if show_panel:
        new_root["PageMode"] = Name("UseOutlines")
    bodies.append((new_root_num, serialize_object(new_root)))

    # ---- lay the objects out after the original bytes --------------------
    chunks: list[bytes] = []
    offsets: dict[int, int] = {}
    position = base_offset
    for num, body in bodies:
        blob = b"%d 0 obj\n" % num + body + b"\nendobj\n"
        offsets[num] = position
        position += len(blob)
        chunks.append(blob)

    # ---- cross-reference section ----------------------------------------
    extra_entries: list[tuple[int, int, int]] = []
    if doc.rebuilt:
        # No usable earlier index exists, so this section must be self-contained.
        for num in sorted(doc.xref):
            kind, a, _b = doc.xref[num]
            if kind == "n" and num not in offsets:
                extra_entries.append((num, a, 0))

    new_entries = sorted(
        [(num, offsets[num], 0) for num in offsets] + extra_entries,
        key=lambda item: item[0],
    )

    root_ref = Ref(new_root_num)
    common: dict = {}
    for key in TRAILER_WHITELIST:
        if key in trailer and key != "Root" and key != "Size":
            common[key] = trailer[key]
    common["Root"] = root_ref
    common["Size"] = new_size
    if doc.xref_offsets:
        common["Prev"] = doc.xref_offsets[0]

    if use_stream:
        # A cross-reference stream must also index itself, so its own entry is
        # added at the offset where the object is about to be written.
        stream_entries = sorted(
            new_entries + [(xref_num, position, 0)], key=lambda item: item[0]
        )
        payload = bytearray()
        width = _offset_width(max(off for _n, off, _g in stream_entries))
        for _num, off, gen in stream_entries:
            payload += b"\x01"
            payload += off.to_bytes(width, "big")
            payload += (gen & 0xFF).to_bytes(1, "big")
        compressed = zlib.compress(bytes(payload), 9)
        index: list[int] = []
        for start, count in _runs(stream_entries):
            index.extend((start, count))
        xref_dict = dict(common)
        xref_dict["Type"] = Name("XRef")
        xref_dict["W"] = [1, width, 1]
        xref_dict["Index"] = index
        xref_dict["Filter"] = Name("FlateDecode")
        xref_dict["Length"] = len(compressed)
        blob = (
            b"%d 0 obj\n" % xref_num
            + serialize_object(xref_dict)
            + b"\nstream\n"
            + compressed
            + b"\nendstream\nendobj\n"
        )
        xref_offset = position
        chunks.append(blob)
        position += len(blob)
    else:
        table = bytearray(b"xref\n")
        entries = new_entries
        i = 0
        while i < len(entries):
            j = i
            while j + 1 < len(entries) and entries[j + 1][0] == entries[j][0] + 1:
                j += 1
            table += b"%d %d\n" % (entries[i][0], j - i + 1)
            for k in range(i, j + 1):
                _num, off, gen = entries[k]
                table += b"%010d %05d n \n" % (off & 0xFFFFFFFF, gen & 0xFFFF)
            i = j + 1
        xref_offset = position
        table += b"trailer\n" + serialize_object(common) + b"\n"
        chunks.append(bytes(table))
        position += len(table)

    chunks.append(b"startxref\n%d\n%%%%EOF\n" % xref_offset)
    return prefix + b"".join(chunks)


def _walk_items(items):
    for item in items:
        yield item
        yield from _walk_items(item.children)


def _resolve_pages(items, out: dict) -> int | None:
    """Record each node's effective page; return the first page in this subtree."""
    first = None
    for item in items:
        child_first = _resolve_pages(item.children, out)
        page = item.page if item.page is not None else child_first
        out[id(item)] = page
        if first is None and page is not None:
            first = page
    return first


def _uses_xref_stream(doc: PDFDocument) -> bool:
    """True when the newest cross-reference section is a stream."""
    if not doc.xref_offsets:
        return False
    offset = doc.xref_offsets[0]
    probe = doc.data[offset : offset + 32].lstrip(b"\x00\t\n\x0c\r ")
    return not probe.startswith(b"xref")


def _offset_width(max_offset: int) -> int:
    return max(1, (max(max_offset, 1).bit_length() + 7) // 8)


def _runs(entries):
    """Group ascending ``(num, off, gen)`` entries into contiguous runs."""
    if not entries:
        return []
    runs = []
    start = prev = entries[0][0]
    for num, _off, _gen in entries[1:]:
        if num == prev + 1:
            prev = num
            continue
        runs.append((start, prev - start + 1))
        start = prev = num
    runs.append((start, prev - start + 1))
    return runs
