"""Command line interface for pdfbookmarks.

The actual import logic lives in :func:`import_bookmarks` so that both the CLI
and the GUI drive exactly the same code path.
"""

from __future__ import annotations

import argparse
import os
import sys

from .outline import add_outline, count_items, flatten_to_tree
from .outline_read import count_outline, format_outline, read_outline
from .pdfdoc import PDFDocument, PDFError
from .textparse import parse_bookmark_text, roman_to_int

__all__ = ["main", "import_bookmarks", "ImportReport", "ImportFailure"]

# Tried in order; gb18030 is deliberately after the strict UTF-8 attempts so a
# genuine UTF-8 file is never mis-decoded.
_FALLBACK_ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "big5", "latin-1")


class ImportFailure(Exception):
    """A problem that should be reported to the user without a traceback."""


class ImportReport:
    """What :func:`import_bookmarks` produced."""

    __slots__ = (
        "output_path",
        "bookmark_count",
        "top_level_count",
        "page_count",
        "warnings",
        "page_style",
        "parsed_total",
        "outline_read_back",
        "tree",
    )

    def __init__(self, **kwargs):
        for key in self.__slots__:
            setattr(self, key, kwargs.get(key))

    def summary(self) -> str:
        lines = [
            "bookmarks written : %s" % self.bookmark_count,
            "top-level items   : %s" % self.top_level_count,
            "pages in PDF      : %s" % self.page_count,
            "page number style : %s" % self.page_style,
            "output            : %s" % self.output_path,
        ]
        return "\n".join(lines)


def read_text_file(path: str, encoding: str | None = None) -> str:
    """Read a bookmark text file, detecting its encoding when not given."""
    with open(path, "rb") as handle:
        raw = handle.read()
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return raw.decode("utf-16")
    if encoding:
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError) as exc:
            raise ImportFailure("cannot decode %s as %s: %s" % (path, encoding, exc))
    for candidate in _FALLBACK_ENCODINGS:
        try:
            return raw.decode(candidate)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def default_output_path(pdf_path: str) -> str:
    stem, ext = os.path.splitext(pdf_path)
    return stem + "_bookmarked" + (ext or ".pdf")


def import_bookmarks(
    pdf_path: str,
    text_path: str,
    output_path: str | None = None,
    *,
    text: str | None = None,
    text_label: str | None = None,
    page_offset: int = 0,
    page_style: str = "auto",
    default_page: int = 1,
    expanded: bool = True,
    show_panel: bool = True,
    use_page_labels: bool = False,
    clamp: bool = False,
    encoding: str | None = None,
    overwrite: bool = False,
    dry_run: bool = False,
) -> ImportReport:
    """Import a bookmark outline into ``pdf_path`` and write ``output_path``.

    The outline is read from ``text_path`` unless ``text`` is given, in which
    case that string is used directly -- this is what the GUI's paste box feeds
    in, so no temporary file is ever needed.
    """
    if not os.path.isfile(pdf_path):
        raise ImportFailure("PDF not found: %s" % pdf_path)

    if text is None:
        if not text_path:
            raise ImportFailure("no bookmark text was supplied")
        if not os.path.isfile(text_path):
            raise ImportFailure("bookmark text not found: %s" % text_path)
        source_name = text_path
        text = read_text_file(text_path, encoding)
    else:
        source_name = text_label or "the pasted text"

    output_path = output_path or default_output_path(pdf_path)
    if os.path.abspath(output_path) == os.path.abspath(pdf_path):
        raise ImportFailure(
            "the output path is the same as the input PDF; choose another file "
            "so the original is preserved"
        )
    if os.path.exists(output_path) and not overwrite and not dry_run:
        raise ImportFailure(
            "output already exists: %s (pass --force to overwrite)" % output_path
        )

    with open(pdf_path, "rb") as handle:
        data = handle.read()

    try:
        doc = PDFDocument(data)
    except PDFError as exc:
        raise ImportFailure("cannot read the PDF: %s" % exc)

    if doc.is_encrypted:
        raise ImportFailure(
            "this PDF is encrypted; decrypt it (e.g. print to a new PDF) first"
        )

    page_count = doc.page_count()
    if page_count == 0:
        raise ImportFailure("the PDF has no pages")

    parsed = parse_bookmark_text(text, page_style=page_style)
    if not parsed.entries:
        raise ImportFailure(
            "no bookmarks found in %s -- each line should look like "
            "'Chapter title 12'" % source_name
        )

    warnings = [str(issue) for issue in parsed.issues]

    def resolve_page(token):
        if token is None:
            return None
        token = str(token).strip()
        if not token:
            return None
        if use_page_labels:
            index = doc.page_index_for_label(token)
            if index is not None:
                return index
        if token.isdigit():
            return int(token) - 1 + page_offset
        value = roman_to_int(token)
        if value is not None:
            return value - 1 + page_offset
        return None

    entries = []
    problems = []
    clamped = 0
    for depth, title, token in parsed.entries:
        index = resolve_page(token)
        if index is not None and not (0 <= index < page_count):
            if clamp:
                index = min(max(index, 0), page_count - 1)
                clamped += 1
            else:
                problems.append((title, token, index + 1))
        entries.append((depth, title, index))

    if problems:
        detail = "; ".join(
            "%r -> page %s (computed %d)" % (title, token, computed)
            for title, token, computed in problems[:5]
        )
        more = "" if len(problems) <= 5 else " (+%d more)" % (len(problems) - 5)
        raise ImportFailure(
            "%d bookmark(s) point outside the document (1-%d pages): %s%s\n"
            "Use --page-offset to shift the numbering, or --clamp to pin them "
            "to the nearest page." % (len(problems), page_count, detail, more)
        )

    if clamped:
        warnings.append("%d bookmark(s) were clamped into the page range" % clamped)

    tree = flatten_to_tree(entries, default_page=max(0, default_page - 1))

    outline_read_back = None
    if dry_run:
        return ImportReport(
            output_path=None,
            bookmark_count=count_items(tree),
            top_level_count=len(tree),
            page_count=page_count,
            warnings=warnings,
            page_style=parsed.page_style_used,
            parsed_total=len(parsed.entries),
            outline_read_back=None,
            tree=tree,
        )

    try:
        result = add_outline(
            doc, tree, expanded=expanded, show_panel=show_panel
        )
    except PDFError as exc:
        raise ImportFailure("cannot write the outline: %s" % exc)

    tmp_path = output_path + ".part"
    try:
        with open(tmp_path, "wb") as handle:
            handle.write(result)
        os.replace(tmp_path, output_path)
    except OSError as exc:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
        raise ImportFailure("cannot write %s: %s" % (output_path, exc))

    # Verify by reading our own output back, so a corrupt result is never
    # reported as success.
    try:
        check = PDFDocument(open(output_path, "rb").read())
        outline = check.dget(check.root, "Outlines")
        outline_read_back = isinstance(outline, dict) and outline.get("First") is not None
        if not outline_read_back:
            warnings.append("verification could not find the outline in the output")
        elif check.page_count() != page_count:
            warnings.append(
                "page count changed after writing (%d -> %d)"
                % (page_count, check.page_count())
            )
    except Exception as exc:  # pragma: no cover - defensive
        warnings.append("verification failed to re-read the output: %s" % exc)

    return ImportReport(
        output_path=output_path,
        bookmark_count=count_items(tree),
        top_level_count=len(tree),
        page_count=page_count,
        warnings=warnings,
        page_style=parsed.page_style_used,
        parsed_total=len(parsed.entries),
        outline_read_back=outline_read_back,
        tree=tree,
    )


# ---------------------------------------------------------------------------
# CLI plumbing
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pdfbookmarks",
        description=(
            "Import a text outline into a PDF as bookmarks.  The original PDF "
            "is never modified: the outline is appended as an incremental "
            "update, and every original byte is preserved."
        ),
        epilog=(
            "examples:\n"
            "  pdfbookmarks book.pdf outline.txt\n"
            "  pdfbookmarks book.pdf outline.txt -o book_new.pdf --page-offset -2\n"
            "  pdfbookmarks book.pdf outline.txt --dry-run\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("pdf", help="the PDF to add bookmarks to")
    parser.add_argument(
        "bookmarks",
        nargs="?",
        help="the text file holding the outline (not needed with --list)",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="just print the bookmarks already inside the PDF, then exit",
    )
    parser.add_argument(
        "-o", "--output", help="output PDF (default: <input>_bookmarked.pdf)"
    )
    parser.add_argument(
        "--page-offset",
        type=int,
        default=0,
        metavar="N",
        help="added to every page number from the text file (use a negative "
        "value when the text counts from a different origin)",
    )
    parser.add_argument(
        "--page-style",
        choices=("auto", "decimal", "roman", "none"),
        default="auto",
        help="how to read trailing page numbers (default: auto)",
    )
    parser.add_argument(
        "--default-page",
        type=int,
        default=1,
        metavar="N",
        help="1-based page for bookmarks with no page number (default: 1)",
    )
    parser.add_argument(
        "--use-page-labels",
        action="store_true",
        help="resolve page numbers through the PDF's own /PageLabels first",
    )
    parser.add_argument(
        "--clamp",
        action="store_true",
        help="pin out-of-range page numbers to the nearest page instead of failing",
    )
    parser.add_argument(
        "--collapsed", action="store_true", help="write bookmarks collapsed"
    )
    parser.add_argument(
        "--no-panel",
        action="store_true",
        help="do not force the reader to open with the bookmark panel showing",
    )
    parser.add_argument(
        "--encoding", help="text file encoding (default: auto-detect)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="parse and report without writing anything",
    )
    parser.add_argument(
        "-f", "--force", action="store_true", help="overwrite an existing output"
    )
    parser.add_argument("-q", "--quiet", action="store_true", help="only report errors")
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="show every warning"
    )
    return parser


def _emit(text: str) -> None:
    try:
        print(text)
    except UnicodeEncodeError:
        # A console with a legacy code page cannot render the text; fall back
        # to raw UTF-8 bytes rather than crashing the run.
        try:
            sys.stdout.buffer.write(text.encode("utf-8", "replace") + b"\n")
            sys.stdout.buffer.flush()
        except Exception:
            pass


def _bookmarks_to_tuples(items):
    """Adapt a ``Bookmark`` forest to the ``(title, page, children)`` shape."""
    return [
        (item.title, item.page, _bookmarks_to_tuples(item.children)) for item in items
    ]


def _list_outline(pdf_path: str) -> int:
    try:
        with open(pdf_path, "rb") as handle:
            doc = PDFDocument(handle.read())
    except OSError as exc:
        _emit("error: cannot open %s: %s" % (pdf_path, exc))
        return 2
    except PDFError as exc:
        _emit("error: cannot read %s: %s" % (pdf_path, exc))
        return 2

    tree = read_outline(doc)
    if not tree:
        _emit("%s has no bookmarks (%d page(s))" % (pdf_path, doc.page_count()))
        return 0
    _emit(
        "%s: %d page(s), %d bookmark(s)"
        % (pdf_path, doc.page_count(), count_outline(tree))
    )
    for line in format_outline(tree):
        _emit(line)
    return 0


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.list:
        return _list_outline(args.pdf)

    if not args.bookmarks:
        parser.error("a bookmark text file is required (or pass --list)")

    try:
        report = import_bookmarks(
            args.pdf,
            args.bookmarks,
            args.output,
            page_offset=args.page_offset,
            page_style=args.page_style,
            default_page=args.default_page,
            expanded=not args.collapsed,
            show_panel=not args.no_panel,
            use_page_labels=args.use_page_labels,
            clamp=args.clamp,
            encoding=args.encoding,
            overwrite=args.force,
            dry_run=args.dry_run,
        )
    except ImportFailure as exc:
        _emit("error: %s" % exc)
        return 2

    if args.dry_run:
        if not args.quiet:
            _emit(
                "dry run: parsed %d bookmark(s), %d top-level item(s), "
                "%d page(s) in the PDF" % (
                    report.bookmark_count,
                    report.top_level_count,
                    report.page_count,
                )
            )
            lines = format_outline(_bookmarks_to_tuples(report.tree or []))
            limit = len(lines) if args.verbose else 40
            for line in lines[:limit]:
                _emit(line)
            if len(lines) > limit:
                _emit("... %d more (pass -v to see all)" % (len(lines) - limit))
    elif not args.quiet:
        _emit("done.")
        _emit(report.summary())

    if report.warnings and not args.quiet:
        shown = report.warnings if args.verbose else report.warnings[:3]
        for warning in shown:
            _emit("warning: %s" % warning)
        if len(report.warnings) > len(shown):
            _emit(
                "warning: %d more warning(s); pass -v to see them all"
                % (len(report.warnings) - len(shown))
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
