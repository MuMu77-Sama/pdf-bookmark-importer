# PDF 书签导入工具（pdfbookmarks）

把**文本形式的目录/书签**导入 PDF，生成阅读器左侧可点击的书签树（PDF outline）。

* **零依赖**：只用 Python 标准库，不需要 `pip install` 任何东西。
* **不破坏原文件**：书签以 *增量更新*（incremental update）方式追加，原 PDF 的每一个字节都原样保留。
* **中文无忧**：标题自动使用 UTF-16BE 编码，中文/日文/emoji 都能正确显示。
* **兼容性广**：支持传统 `xref` 表、交叉引用流（xref stream）、对象流（objstm）三种 PDF 布局；xref 损坏时还能自动重建索引。
* **两种用法**：命令行 + 图形界面（tkinter）。

---

## 快速开始

### 图形界面

双击 **`pdfbookmarks-gui.cmd`**。

1. 选择 PDF；
2. 选择书签来源，**两种方式任选**：
   * **从文件导入** —— 点「浏览…」选 `.txt` 文件；
   * **直接粘贴文本** —— 选这一项后把目录内容直接粘进文本框，不用先存成文件；
3. 选输出文件，点「预览解析结果」确认层级和页码，再点「写入书签」。

### 命令行

```bat
pdfbookmarks.cmd 书.pdf 目录.txt
pdfbookmarks.cmd 书.pdf 目录.txt -o 新书.pdf --page-offset -2
pdfbookmarks.cmd 书.pdf --list
```

不带参数时会显示完整帮助：

```bat
pdfbookmarks.cmd --help
```

也可以直接用 Python 调用（任何平台）：

```bash
python -m pdfbookmarks 书.pdf 目录.txt
```

非 Windows 平台用附带的 shell 启动器（第一次用先 `chmod +x`）：

```bash
./pdfbookmarks.sh 书.pdf 目录.txt     # 命令行
./pdfbookmarks-gui.sh                 # 图形界面
```

---

## 书签文本格式

工具会**自动识别**多种常见写法，不需要手动指定格式。

### 1. 缩进大纲（最常用）

靠行首的空格或 Tab 表示层级，行尾数字是页码：

```
第一章 绪论 1
  1.1 研究背景 1
  1.2 研究意义 2
第二章 相关工作 3
  2.1 国内研究 3
    2.1.1 早期工作 3
```

### 2. Markdown

```
# Chapter 1 Introduction
## 1.1 Background
- Motivation
  - Why it matters
# Chapter 2 Method
```

`#` 的个数决定层级，列表项自动比所在标题深一级。
注意：Markdown 标题里**纯空格分隔的数字不会被当作页码**，所以
`# Chapter 1` 的标题完整保留为 "Chapter 1"。要指定页码请用
`# Chapter 1 ......12`、`# Chapter 1 | 12` 或 `# Chapter 1 p12`。

### 3. 编号大纲（没有缩进也能认层级）

```
1 绪论 1
1.1 背景 1
1.1.1 编码器 2
2 方法 3
```

### 4. 点线 / 制表符 / 竖线 / CSV

```
第一章 绪论....................1
第二章 相关工作................3
```

```
Introduction	1
Background	2
```

```
Alpha, 3
Beta, 5
```

### 5. 中文标题

```
第一章 绪论 1      -> 第 0 层
第二章 方法 5      -> 第 0 层
```

### 分隔符与缩进规则

* **标题和页码之间**：半角空格、**全角空格（U+3000）**、Tab、点线 `......`、
  竖线 `|`、破折号都可以。
* **编号和标题之间**：同样半角/全角都认，所以 `10.1 引言` 和 `10.1　引言`
  完全等价。中日文书签里常见的全角空格不会再让层级塌掉。
* **层级**由缩进或编号深度决定：
  * 缩进可以用半角空格、Tab 或全角空格；
  * 如果每行都缩进相同的量，会自动把最外层当作第 0 层，
    不会把所有条目串成一条链。

### 页码写法

| 写法 | 说明 |
| --- | --- |
| `标题 12` | 空格分隔（普通行、列表项） |
| `标题......12` | 点线 |
| `标题 \| 12` | 竖线 |
| `标题 - 12` | 破折号 |
| `标题 p12` / `标题 page 12` | 显式标记 |
| `标题 第 12 页` | 中文显式标记 |
| `标题 xii` | 罗马数字（自动识别或 `--page-style roman`） |

> `方法2` 不会被拆开（数字紧贴文字不算页码），而 `方法 2` 会解析为标题「方法」+ 第 2 页。

### 注释与空行

以 `//`、`;` 或 `#!` 开头的行会被忽略，空行也会忽略。

---

## 命令行选项

| 选项 | 说明 |
| --- | --- |
| `-o, --output` | 输出文件，默认 `<原文件名>_bookmarked.pdf` |
| `--page-offset N` | 给文本里所有页码加上 N。适合「书内页码 ≠ PDF 物理页码」的情况，可以是负数 |
| `--page-style` | `auto`（默认）/ `decimal` / `roman` / `none` |
| `--default-page N` | 没有页码的书签指向第 N 页（默认 1） |
| `--use-page-labels` | 优先按 PDF 自带的 `/PageLabels` 解析页码（能处理前言用罗马数字、正文用阿拉伯数字的文档） |
| `--clamp` | 页码越界时钳制到最近的有效页，而不是报错 |
| `--collapsed` | 书签默认折叠 |
| `--no-panel` | 不强制阅读器打开书签面板 |
| `--encoding` | 指定文本编码，默认自动检测（UTF-8 / UTF-8-BOM / UTF-16 / GB18030 / Big5） |
| `--dry-run` | 只解析并打印结果，不写文件 |
| `--list` | 打印 PDF 里现有的书签，不做任何修改 |
| `-f, --force` | 覆盖已存在的输出文件 |
| `-q` / `-v` | 安静模式 / 显示全部警告 |

### 常见问题：页码对不上

如果文本里的页码是书的印刷页码，而 PDF 前面有封面、目录等未编号页，
用偏移量修正：

```bat
pdfbookmarks.cmd 书.pdf 目录.txt --page-offset 8
```

先用 `--dry-run` 看一下算出来的页码是否正确：

```bat
pdfbookmarks.cmd 书.pdf 目录.txt --dry-run
```

---

## 环境要求

* **Python 3.8 或更高版本**，Windows / macOS / Linux 均可。
* **零第三方依赖** —— 全部只用标准库（`zlib`、`re`、`argparse`、`tkinter` 等），
  **不需要 `pip install` 任何东西**，拷贝到别的机器就能跑。
* 图形界面需要 `tkinter`：Windows 和 macOS 的官方 Python 自带；
  Debian/Ubuntu 上需要 `sudo apt install python3-tk`。

启动器 `pdfbookmarks.cmd` / `pdfbookmarks-gui.cmd` 会自动寻找可用的
Python，并会把结果打印出来（如果失败）。

### 关于本机的 Python 环境

本机的 Python 3.14 安装是**不完整**的：

* `C:\Python314` 有解释器 `python.exe`，但**缺少 `Lib` 标准库目录**；
* `%LOCALAPPDATA%\Programs\Python\Python314` 有完整的 `Lib`、`DLLs`、`tcl`，但**没有 `python.exe`**。

`tools\find_python.ps1` 会自动把这两半配对起来（用 `PYTHONHOME` 指向
含标准库的那一半），所以本工具开箱即用，无需你手动修复。

如果想彻底修好本机 Python（让 `python` 命令到处都能用），可以任选其一：

1. 把 `%LOCALAPPDATA%\Programs\Python\Python314\Lib` 复制到 `C:\Python314\Lib`；
2. 或者重新安装一次 Python 3.14（勾选 "Add python.exe to PATH"）。

也可以用环境变量显式指定解释器：

```bat
set PDFBOOKMARKS_PYTHON=C:\some\path\python.exe
```

---

## 测试

```bat
python tests\test_all.py
```

测试覆盖：

* 三种 xref 布局（传统表 / xref 流 / 对象流）的读取与写入往返；
* 用一个**独立实现**的校验器逐条核对输出文件的 xref 偏移是否真的指向对应对象；
* 书签树的 `/First` `/Last` `/Next` `/Prev` `/Parent` `/Count` 结构一致性；
* 中文、法文、emoji 标题的编码往返；
* xref 损坏后的重建路径；
* 360 条书签的大纲；
* 文本解析的各种格式；
* 命令行与图形界面的端到端流程。

样例 PDF 可以用下面的命令重新生成（`samples\` 下已有生成好的）：

```bat
python tools\make_sample_pdf.py
```

---

## 工作原理

写入采用 PDF 的**增量更新**机制：

1. 解析原文件的交叉引用（`xref` 表或 xref 流），沿 `/Prev` 链收集所有对象位置；
2. 从页面树（`/Root` → `/Pages` → `/Kids`）按顺序取出每一页的对象引用；
3. 在文件**末尾追加**新的对象：大纲根节点、每个书签、以及一份改写了 `/Outlines` 和 `/PageMode` 的 `/Root` 副本；
4. 追加一段新的交叉引用（形式与原文件保持一致：原来用传统表就用传统表，原来用 xref 流就用 xref 流），`/Prev` 指回原来的 `startxref`；
5. 追加新的 `startxref` 和 `%%EOF`。

因此：

* 原有的内容流、字体、图片、注释一个字节都不会被重写；
* 文件大小只增加书签本身的开销；
* 生成的 PDF 可以被任何标准阅读器打开。

### 限制

* **加密 PDF 不支持**。请先解密，或用阅读器「打印为新 PDF」后再生作。
* 会**替换**已有书签（不是追加到原有书签后面）。
* 已经数字签名的 PDF，写入后签名会失效（这是任何修改都无法避免的）。
* 线性化（fast web view）PDF 写入后不再是线性化的，但仍然完全有效。

---

## 目录结构

```
pdfbookmarks.cmd         Windows 命令行启动器
pdfbookmarks-gui.cmd     Windows 图形界面启动器
pdfbookmarks.sh          Linux / macOS 命令行启动器
pdfbookmarks-gui.sh      Linux / macOS 图形界面启动器
pdfbookmarks/
  pdfobj.py       PDF 对象模型与语法解析（含 zlib/LZW/预测器解码）
  pdfdoc.py       文档层：xref 表、xref 流、对象流、页面树、损坏重建
  outline.py      书签写入（增量更新）
  outline_read.py 读取已有书签
  textparse.py    书签文本的通用解析
  cli.py          命令行接口 + 核心导入逻辑
  gui.py          tkinter 图形界面
tools/
  make_sample_pdf.py  生成测试用样例 PDF
  find_python.ps1     Windows 上自动探测可用的 Python
tests/
  test_all.py         测试套件
samples/              样例 PDF 与书签文本
```
