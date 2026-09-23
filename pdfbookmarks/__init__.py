"""pdfbookmarks - import a text outline into a PDF as bookmarks.

Pure standard library, no third-party dependencies.

Typical use::

    from pdfbookmarks import PDFDocument, add_outline, Bookmark

    doc = PDFDocument(open("in.pdf", "rb").read())
    tree = [Bookmark("Chapter 1", 0), Bookmark("Chapter 2", 4)]
    out = doc.data + add_outline(doc, tree)
    open("out.pdf", "wb").write(out)
"""

from .pdfdoc import PDFDocument, PDFError
from .outline import Bookmark, add_outline, count_items, flatten_to_tree
from .textparse import ParseIssue, ParseResult, parse_bookmark_text

__version__ = "1.0.0"

__all__ = [
    "PDFDocument",
    "PDFError",
    "Bookmark",
    "add_outline",
    "count_items",
    "flatten_to_tree",
    "parse_bookmark_text",
    "ParseResult",
    "ParseIssue",
    "__version__",
]
