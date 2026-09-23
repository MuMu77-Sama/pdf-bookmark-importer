"""Read an existing bookmark outline out of a PDF."""

from __future__ import annotations

from .pdfobj import Name, Ref, decode_pdf_text_string

__all__ = ["read_outline", "count_outline", "format_outline"]


def read_outline(doc) -> list:
    """Return the outline as ``[(title, page_index, [children...])]``.

    ``page_index`` is a zero-based physical page index, or ``None`` for a
    bookmark that only acts as a container.
    """
    outline = doc.dget(doc.root, "Outlines")
    if not isinstance(outline, dict):
        return []
    pages = doc.pages

    def dest_page(item):
        dest = doc.dget(item, "Dest")
        if dest is None:
            action = doc.dget(item, "A")
            if isinstance(action, dict):
                dest = doc.dget(action, "D")
        if isinstance(dest, Name):
            return None  # a named destination we would have to look up
        if isinstance(dest, list) and dest:
            target = dest[0]
            if isinstance(target, Ref) and target in pages:
                return pages.index(target)
        return None

    def walk(first, depth=0):
        out = []
        ref = first
        guard = 0
        seen = set()
        while ref is not None and guard < 10000:
            guard += 1
            key = ref.num if isinstance(ref, Ref) else id(ref)
            if key in seen:
                break
            seen.add(key)
            item = doc.resolve(ref)
            if not isinstance(item, dict):
                break
            title = decode_pdf_text_string(doc.resolve(item.get("Title")) or b"")
            out.append((title, dest_page(item), walk(item.get("First"), depth + 1)))
            ref = item.get("Next")
        return out

    return walk(outline.get("First"))


def count_outline(items) -> int:
    return sum(1 + count_outline(item[2]) for item in items)


def format_outline(items, indent: str = "  ", level: int = 0) -> list:
    """Render an outline as printable lines."""
    lines = []
    for title, page, children in items:
        location = "" if page is None else "  ....... p.%d" % (page + 1)
        lines.append("%s%s%s" % (indent * level, title, location))
        lines.extend(format_outline(children, indent, level + 1))
    return lines
