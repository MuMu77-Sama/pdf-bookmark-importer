"""A small tkinter front-end for pdfbookmarks.

The window drives exactly the same :func:`pdfbookmarks.cli.import_bookmarks`
code as the command line, so both paths behave identically.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import tkinter as tk
import traceback
from tkinter import filedialog, messagebox, ttk

from .cli import ImportFailure, default_output_path, import_bookmarks
from .outline_read import count_outline, read_outline
from .pdfdoc import PDFDocument

__all__ = ["main", "BookmarkApp"]

APP_TITLE = "PDF 书签导入工具 / PDF Bookmark Importer"

PAGE_STYLES = [
    ("自动识别 (auto)", "auto"),
    ("阿拉伯数字 (decimal)", "decimal"),
    ("罗马数字 (roman)", "roman"),
    ("忽略页码 (none)", "none"),
]


def _enable_dpi_awareness() -> None:
    """Keep the window crisp on high-DPI Windows displays."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


class BookmarkApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(APP_TITLE)
        self.root.minsize(720, 560)

        self.pdf_var = tk.StringVar()
        self.text_var = tk.StringVar()
        self.out_var = tk.StringVar()
        self.source_var = tk.StringVar(value="file")
        self.offset_var = tk.StringVar(value="0")
        self.default_page_var = tk.StringVar(value="1")
        self.style_var = tk.StringVar(value=PAGE_STYLES[0][0])
        self.expanded_var = tk.BooleanVar(value=True)
        self.panel_var = tk.BooleanVar(value=True)
        self.labels_var = tk.BooleanVar(value=False)
        self.clamp_var = tk.BooleanVar(value=False)
        self.encoding_var = tk.StringVar()

        self.status_var = tk.StringVar(value="选择 PDF 和书签文本，然后点击“预览”。")

        # True while the output path is still being derived automatically.
        self._auto_output = True

        self._build()
        self._apply_fonts()

    # ------------------------------------------------------------------
    # layout
    # ------------------------------------------------------------------
    def _apply_fonts(self) -> None:
        try:
            from tkinter import font as tkfont

            for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont"):
                tkfont.nametofont(name).configure(family="Microsoft YaHei UI", size=9)
            tkfont.nametofont("TkFixedFont").configure(size=9)
        except Exception:
            pass

    def _build(self) -> None:
        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(1, weight=1)

        row = 0
        row = self._file_row(outer, row, "PDF 文件", self.pdf_var, self._pick_pdf)
        row = self._build_text_source(outer, row)
        row = self._file_row(outer, row, "输出 PDF", self.out_var, self._pick_output)

        ttk.Separator(outer, orient="horizontal").grid(
            row=row, column=0, columnspan=3, sticky="ew", pady=(10, 8)
        )
        row += 1

        options = ttk.LabelFrame(outer, text="选项", padding=10)
        options.grid(row=row, column=0, columnspan=3, sticky="ew")
        options.columnconfigure(1, weight=1)
        options.columnconfigure(3, weight=1)
        row += 1

        ttk.Label(options, text="页码偏移").grid(row=0, column=0, sticky="w")
        ttk.Spinbox(
            options, from_=-500, to=500, width=8, textvariable=self.offset_var
        ).grid(row=0, column=1, sticky="w", padx=(6, 18))
        ttk.Label(options, text="无页码时指向第").grid(row=0, column=2, sticky="w")
        ttk.Spinbox(
            options, from_=1, to=100000, width=8, textvariable=self.default_page_var
        ).grid(row=0, column=3, sticky="w", padx=(6, 0))

        ttk.Label(options, text="页码样式").grid(
            row=1, column=0, sticky="w", pady=(8, 0)
        )
        style_box = ttk.Combobox(
            options,
            textvariable=self.style_var,
            values=[label for label, _value in PAGE_STYLES],
            state="readonly",
            width=20,
        )
        style_box.grid(row=1, column=1, sticky="w", padx=(6, 18), pady=(8, 0))

        ttk.Checkbutton(
            options, text="书签默认展开", variable=self.expanded_var
        ).grid(row=1, column=2, columnspan=2, sticky="w", pady=(8, 0))

        ttk.Checkbutton(
            options, text="打开 PDF 时显示书签面板", variable=self.panel_var
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Checkbutton(
            options, text="优先使用 PDF 页标签", variable=self.labels_var
        ).grid(row=2, column=2, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Checkbutton(
            options,
            text="页码越界时钳制到最近页（否则报错）",
            variable=self.clamp_var,
        ).grid(row=3, column=0, columnspan=4, sticky="w", pady=(6, 0))

        ttk.Label(options, text="文本编码").grid(
            row=4, column=0, sticky="w", pady=(8, 0)
        )
        ttk.Entry(options, textvariable=self.encoding_var, width=14).grid(
            row=4, column=1, sticky="w", padx=(6, 0), pady=(8, 0)
        )
        ttk.Label(options, text="留空自动检测", foreground="#666").grid(
            row=4, column=2, columnspan=2, sticky="w", pady=(8, 0)
        )

        buttons = ttk.Frame(outer)
        buttons.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(12, 8))
        row += 1
        self.preview_button = ttk.Button(
            buttons, text="预览解析结果", command=self.on_preview
        )
        self.preview_button.pack(side="left")
        self.import_button = ttk.Button(
            buttons, text="写入书签", command=self.on_import
        )
        self.import_button.pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="查看 PDF 现有书签", command=self.on_list).pack(
            side="left", padx=(8, 0)
        )
        ttk.Button(buttons, text="退出", command=self.root.destroy).pack(side="right")

        tree_frame = ttk.LabelFrame(outer, text="书签预览", padding=6)
        tree_frame.grid(row=row, column=0, columnspan=3, sticky="nsew")
        outer.rowconfigure(row, weight=1)
        row += 1

        self.tree = ttk.Treeview(
            tree_frame, columns=("page",), selectmode="browse", height=14
        )
        self.tree.heading("#0", text="标题")
        self.tree.heading("page", text="页码")
        self.tree.column("#0", width=460, stretch=True)
        self.tree.column("page", width=80, anchor="center", stretch=False)

        scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        status = ttk.Label(
            outer, textvariable=self.status_var, anchor="w", foreground="#333"
        )
        status.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(8, 0))

    def _file_row(self, parent, row, label, variable, command) -> int:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=3)
        ttk.Entry(parent, textvariable=variable).grid(
            row=row, column=1, sticky="ew", padx=(6, 6), pady=3
        )
        ttk.Button(parent, text="浏览…", command=command, width=8).grid(
            row=row, column=2, sticky="e", pady=3
        )
        return row + 1

    def _build_text_source(self, parent, row: int) -> int:
        """Offer both a file picker and a paste box; the radio picks the winner."""
        frame = ttk.LabelFrame(parent, text="书签文本", padding=8)
        frame.grid(row=row, column=0, columnspan=3, sticky="ew", pady=3)
        frame.columnconfigure(2, weight=1)

        ttk.Radiobutton(
            frame,
            text="从文件导入",
            value="file",
            variable=self.source_var,
            command=self._on_source_change,
        ).grid(row=0, column=0, sticky="w")
        self.text_entry = ttk.Entry(frame, textvariable=self.text_var)
        self.text_entry.grid(row=0, column=1, columnspan=2, sticky="ew", padx=(8, 6))
        self.text_browse = ttk.Button(
            frame, text="浏览…", command=self._pick_text, width=8
        )
        self.text_browse.grid(row=0, column=3, sticky="e")

        ttk.Radiobutton(
            frame,
            text="直接粘贴文本（忽略上面的文件）",
            value="paste",
            variable=self.source_var,
            command=self._on_source_change,
        ).grid(row=1, column=0, columnspan=4, sticky="w", pady=(8, 4))

        box = ttk.Frame(frame)
        box.grid(row=2, column=0, columnspan=4, sticky="ew")
        box.columnconfigure(0, weight=1)
        self.text_box = tk.Text(
            box, height=7, wrap="none", undo=True, font=("Consolas", 9)
        )
        self.text_box.grid(row=0, column=0, sticky="ew")
        y_scroll = ttk.Scrollbar(box, orient="vertical", command=self.text_box.yview)
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll = ttk.Scrollbar(box, orient="horizontal", command=self.text_box.xview)
        x_scroll.grid(row=1, column=0, sticky="ew")
        self.text_box.configure(
            yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set
        )

        ttk.Label(
            frame,
            text="粘贴时每行写「标题 页码」；缩进或编号深度决定层级。",
            foreground="#666",
        ).grid(row=3, column=0, columnspan=4, sticky="w", pady=(4, 0))

        self._on_source_change()
        return row + 1

    def _on_source_change(self) -> None:
        """Enable only the widgets that belong to the selected source."""
        paste = self.source_var.get() == "paste"
        self.text_entry.configure(state="disabled" if paste else "normal")
        self.text_browse.configure(state="disabled" if paste else "normal")
        self.text_box.configure(state="normal" if paste else "disabled")

    def _source(self):
        """Return ``(text_path, text)`` for the selected source.

        Exactly one of the two is non-``None``, so callers can pass both
        straight through to :func:`import_bookmarks`.
        """
        if self.source_var.get() == "paste":
            return None, self.text_box.get("1.0", "end-1c")
        return self.text_var.get(), None

    # ------------------------------------------------------------------
    # file pickers
    # ------------------------------------------------------------------
    def _pick_pdf(self) -> None:
        path = filedialog.askopenfilename(
            title="选择 PDF",
            filetypes=[("PDF 文件", "*.pdf"), ("所有文件", "*.*")],
        )
        if path:
            self.pdf_var.set(path)
            if self._auto_output or not self.out_var.get():
                self.out_var.set(default_output_path(path))
                self._auto_output = True

    def _pick_text(self) -> None:
        path = filedialog.askopenfilename(
            title="选择书签文本",
            filetypes=[
                ("文本文件", "*.txt *.md *.csv"),
                ("所有文件", "*.*"),
            ],
        )
        if path:
            self.text_var.set(path)
            self.source_var.set("file")
            self._on_source_change()

    def _pick_output(self) -> None:
        initial = self.out_var.get() or (
            default_output_path(self.pdf_var.get()) if self.pdf_var.get() else ""
        )
        path = filedialog.asksaveasfilename(
            title="保存为",
            defaultextension=".pdf",
            initialfile=os.path.basename(initial) if initial else "bookmarked.pdf",
            initialdir=os.path.dirname(initial) if initial else None,
            filetypes=[("PDF 文件", "*.pdf"), ("所有文件", "*.*")],
        )
        if path:
            self.out_var.set(path)
            self._auto_output = False

    # ------------------------------------------------------------------
    # actions
    # ------------------------------------------------------------------
    def _options(self) -> dict:
        label = self.style_var.get()
        style = "auto"
        for text, value in PAGE_STYLES:
            if text == label:
                style = value
                break

        def as_int(variable, fallback):
            try:
                return int(str(variable.get()).strip())
            except (TypeError, ValueError):
                return fallback

        return {
            "page_offset": as_int(self.offset_var, 0),
            "default_page": max(1, as_int(self.default_page_var, 1)),
            "page_style": style,
            "expanded": bool(self.expanded_var.get()),
            "show_panel": bool(self.panel_var.get()),
            "use_page_labels": bool(self.labels_var.get()),
            "clamp": bool(self.clamp_var.get()),
            "encoding": self.encoding_var.get().strip() or None,
        }

    def _require_inputs(self) -> bool:
        if not self.pdf_var.get():
            messagebox.showwarning(APP_TITLE, "请先选择 PDF 文件。")
            return False
        if not os.path.isfile(self.pdf_var.get()):
            messagebox.showerror(APP_TITLE, "找不到 PDF 文件：\n%s" % self.pdf_var.get())
            return False

        path, text = self._source()
        if path is not None:
            if not path:
                messagebox.showwarning(
                    APP_TITLE, "请先选择书签文本文件，或改选「直接粘贴文本」。"
                )
                return False
            if not os.path.isfile(path):
                messagebox.showerror(APP_TITLE, "找不到书签文本：\n%s" % path)
                return False
        elif not (text or "").strip():
            messagebox.showwarning(APP_TITLE, "请在文本框中粘贴书签内容。")
            return False
        return True

    def _show_tree(self, items, parent="") -> int:
        total = 0
        for title, page, children in items:
            node = self.tree.insert(
                parent,
                "end",
                text=title,
                values=("" if page is None else page + 1,),
                open=True,
            )
            total += 1 + self._show_tree(children, node)
        return total

    def on_preview(self) -> None:
        if not self._require_inputs():
            return
        self.tree.delete(*self.tree.get_children())
        self.status_var.set("正在解析…")
        self.root.update_idletasks()
        path, text = self._source()
        try:
            report = import_bookmarks(
                self.pdf_var.get(),
                path,
                text=text,
                text_label="粘贴的文本",
                dry_run=True,
                **self._options(),
            )
        except ImportFailure as exc:
            self.status_var.set("解析失败。")
            messagebox.showerror(APP_TITLE, str(exc))
            return
        except Exception:
            self.status_var.set("解析失败。")
            messagebox.showerror(APP_TITLE, traceback.format_exc())
            return

        shown = self._show_tree(self._bookmark_tuples(report.tree or []))
        self.status_var.set(
            "解析成功：%d 条书签（%d 个顶层），PDF 共 %d 页，页码样式 %s。"
            % (shown, report.top_level_count, report.page_count, report.page_style)
        )
        if report.warnings:
            messagebox.showwarning(
                APP_TITLE, "提示：\n" + "\n".join(report.warnings[:6])
            )

    def _bookmark_tuples(self, items):
        return [
            (item.title, item.page, self._bookmark_tuples(item.children))
            for item in items
        ]

    def on_import(self) -> None:
        if not self._require_inputs():
            return
        output = self.out_var.get().strip() or default_output_path(self.pdf_var.get())
        if os.path.exists(output):
            if not messagebox.askyesno(
                APP_TITLE, "输出文件已存在，要覆盖吗？\n%s" % output
            ):
                return
        self.status_var.set("正在写入…")
        self.root.update_idletasks()
        path, text = self._source()
        try:
            report = import_bookmarks(
                self.pdf_var.get(),
                path,
                output,
                text=text,
                text_label="粘贴的文本",
                overwrite=True,
                **self._options(),
            )
        except ImportFailure as exc:
            self.status_var.set("写入失败。")
            messagebox.showerror(APP_TITLE, str(exc))
            return
        except Exception:
            self.status_var.set("写入失败。")
            messagebox.showerror(APP_TITLE, traceback.format_exc())
            return

        self.out_var.set(report.output_path)
        self.status_var.set(
            "完成：已写入 %d 条书签 → %s" % (report.bookmark_count, report.output_path)
        )
        detail = "已写入 %d 条书签。\n\n输出文件：\n%s" % (
            report.bookmark_count,
            report.output_path,
        )
        if report.warnings:
            detail += "\n\n提示：\n" + "\n".join(report.warnings[:6])
        if report.outline_read_back is False:
            detail += "\n\n注意：回读校验没能确认书签，请检查输出文件。"
        messagebox.showinfo(APP_TITLE, detail)

    def on_list(self) -> None:
        path = self.pdf_var.get()
        if not path or not os.path.isfile(path):
            messagebox.showwarning(APP_TITLE, "请先选择一个存在的 PDF 文件。")
            return
        self.tree.delete(*self.tree.get_children())
        try:
            with open(path, "rb") as handle:
                doc = PDFDocument(handle.read())
            items = read_outline(doc)
        except Exception as exc:
            messagebox.showerror(APP_TITLE, "无法读取书签：%s" % exc)
            return
        if not items:
            self.status_var.set("%s 中没有书签（共 %d 页）。" % (path, doc.page_count()))
            return
        self._show_tree(items)
        self.status_var.set(
            "%s：共 %d 页，%d 条书签。" % (path, doc.page_count(), count_outline(items))
        )


def release_samples(destination: str) -> list:
    """Copy sample files bundled inside the executable out next to it, once.

    Returns the paths actually written -- empty when running from source, when
    the build carries no samples, or when they are already present.
    """
    base = getattr(sys, "_MEIPASS", None)
    if not base:
        return []
    source = os.path.join(base, "samples")
    if not os.path.isdir(source):
        return []
    written = []
    try:
        os.makedirs(destination, exist_ok=True)
        for name in sorted(os.listdir(source)):
            src = os.path.join(source, name)
            dst = os.path.join(destination, name)
            if os.path.isfile(src) and not os.path.exists(dst):
                shutil.copy2(src, dst)
                written.append(dst)
    except OSError:
        # A read-only location (Program Files, a network share) is not fatal.
        return written
    return written


def run_bundled_selfcheck() -> int:
    """End-to-end check inside a frozen build, using the embedded samples.

    Proves the packaged executable can really read the 700-page PDF, parse the
    bookmark text and write a bookmarked PDF -- not merely open a window.

    Returns 0 on success, or a small non-zero code naming the failure stage.
    """
    from .cli import import_bookmarks

    base = getattr(sys, "_MEIPASS", None)
    if not base:
        return 2  # not a frozen build
    pdf = os.path.join(base, "samples", "TestPdfPage__700.pdf")
    txt = os.path.join(base, "samples", "bookmarks_test.txt")
    if not (os.path.isfile(pdf) and os.path.isfile(txt)):
        return 3  # samples missing from the build

    out = os.path.join(tempfile.gettempdir(), "pdfbookmarks_selfcheck.pdf")
    try:
        report = import_bookmarks(pdf, txt, out, overwrite=True)
        if report.bookmark_count != 487:
            return 5
        if report.top_level_count != 19:
            return 6
        return 0
    except Exception:
        return 4  # the import itself failed
    finally:
        try:
            if os.path.exists(out):
                os.remove(out)
        except OSError:
            pass


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    # A frozen build carries the sample files inside the .exe; drop them next
    # to it so the user can find them and point the tool at them.
    if getattr(sys, "frozen", False):
        beside = os.path.dirname(os.path.abspath(sys.executable))
        release_samples(os.path.join(beside, "samples"))

    if "--selfcheck" in argv:
        return run_bundled_selfcheck()

    _enable_dpi_awareness()
    root = tk.Tk()
    BookmarkApp(root)
    if "--selftest" in argv:
        # Build the window, prove it lays out, then exit without blocking.
        root.update_idletasks()
        root.destroy()
        return 0
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
