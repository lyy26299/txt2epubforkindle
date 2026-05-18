import html
import os
import posixpath
import re
import shutil
import tempfile
import threading
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from tkinter import BooleanVar, END, IntVar, StringVar, Text, Tk, Toplevel, filedialog, messagebox, ttk


APP_NAME = "Kindle 小说工具箱"

CHAPTER_RE = re.compile(
    r"^\s*("
    r"第[0-9〇零一二两三四五六七八九十百千万壹贰叁肆伍陆柒捌玖拾佰仟]+[章节卷回部篇集][^\n]{0,40}"
    r"|卷[0-9〇零一二两三四五六七八九十百千万]+[^\n]{0,40}"
    r"|Chapter\s+[0-9IVXLCDM]+[^\n]{0,40}"
    r"|CHAPTER\s+[0-9IVXLCDM]+[^\n]{0,40}"
    r")\s*$"
)

KINDLE_FONT_OPTIONS = (
    ("中文宋体（推荐）", '"Songti SC", "SimSun", "STSong", serif'),
    ("中文黑体", '"Heiti SC", "SimHei", sans-serif'),
    ("中文楷体", '"Kaiti SC", "KaiTi", serif'),
    ("Bookerly", '"Bookerly", serif'),
    ("Amazon Ember", '"Amazon Ember", sans-serif'),
    ("Caecilia", '"Caecilia", serif'),
    ("Palatino", '"Palatino", serif'),
    ("Baskerville", '"Baskerville", serif'),
    ("Helvetica", '"Helvetica", sans-serif'),
    ("Futura", '"Futura", sans-serif'),
    ("OpenDyslexic", '"OpenDyslexic", sans-serif'),
    ("Kindle 默认衬线", "serif"),
    ("Kindle 默认无衬线", "sans-serif"),
)

KINDLE_FONT_MAP = dict(KINDLE_FONT_OPTIONS)


@dataclass
class Chapter:
    title: str
    paragraphs: list[str]


def detect_encoding(path: Path) -> str:
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return "utf-16"

    candidates = ("utf-8", "gb18030", "big5", "utf-16")
    best_encoding = "utf-8"
    best_score = -1
    for encoding in candidates:
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        score = score_text(text)
        if score > best_score:
            best_encoding = encoding
            best_score = score
    return best_encoding


def score_text(text: str) -> int:
    score = 0
    score += sum(1 for c in text if "\u4e00" <= c <= "\u9fff") * 3
    score += sum(1 for c in text if c in "，。！？；：“”‘’（）《》、　") * 2
    score -= text.count("\ufffd") * 20
    score -= sum(1 for c in text if c in "銆€绗竴") * 3
    return score


def read_text(path: Path, encoding: str = "auto") -> tuple[str, str]:
    actual_encoding = detect_encoding(path) if encoding == "auto" else encoding
    try:
        return path.read_text(encoding=actual_encoding), actual_encoding
    except UnicodeDecodeError:
        return path.read_text(encoding="gb18030", errors="replace"), "gb18030"


def normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\ufeff", "")
    lines = [line.rstrip() for line in text.split("\n")]
    return "\n".join(lines).strip()


def split_by_headings(text: str) -> list[Chapter]:
    lines = text.split("\n")
    chapters: list[Chapter] = []
    current_title = "序章"
    current_lines: list[str] = []
    found_heading = False

    for line in lines:
        stripped = line.strip()
        is_heading = bool(stripped and len(stripped) <= 60 and CHAPTER_RE.match(stripped))
        if is_heading:
            found_heading = True
            if current_lines or chapters:
                chapters.append(Chapter(current_title, lines_to_paragraphs(current_lines)))
            current_title = stripped
            current_lines = []
        else:
            current_lines.append(line)

    if current_lines or not chapters:
        chapters.append(Chapter(current_title, lines_to_paragraphs(current_lines)))

    if not found_heading or len(chapters) < 2:
        return []
    return [chapter for chapter in chapters if chapter.paragraphs]


def split_by_size(text: str, target_chars: int) -> list[Chapter]:
    paragraphs = lines_to_paragraphs(text.split("\n"))
    chapters: list[Chapter] = []
    current: list[str] = []
    current_len = 0

    for paragraph in paragraphs:
        paragraph_len = len(paragraph)
        if current and current_len + paragraph_len > target_chars:
            chapters.append(Chapter(f"第 {len(chapters) + 1} 章", current))
            current = []
            current_len = 0
        current.append(paragraph)
        current_len += paragraph_len

    if current:
        chapters.append(Chapter(f"第 {len(chapters) + 1} 章", current))
    return chapters


def lines_to_paragraphs(lines: list[str]) -> list[str]:
    paragraphs: list[str] = []
    buffer: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if buffer:
                paragraphs.append("".join(buffer).strip())
                buffer = []
            continue
        buffer.append(stripped)
    if buffer:
        paragraphs.append("".join(buffer).strip())
    return paragraphs


def build_chapters(text: str, use_headings: bool, target_chars: int) -> tuple[list[Chapter], str]:
    text = normalize_text(text)
    if use_headings:
        chapters = split_by_headings(text)
        if chapters:
            return chapters, "按章节标题自动识别"
    return split_by_size(text, max(100, target_chars)), "未识别到足够章节标题，已按长度自动分章"


def xhtml_page(title: str, body: str) -> str:
    return f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="zh-CN" lang="zh-CN">
<head>
  <meta charset="utf-8"/>
  <title>{html.escape(title)}</title>
  <link rel="stylesheet" type="text/css" href="../styles/style.css"/>
</head>
<body>
{body}
</body>
</html>
"""


def safe_xml(text: str) -> str:
    return html.escape(text, quote=True)


def make_css(font_family: str, embedded_font_name: str | None) -> str:
    font_face = ""
    if embedded_font_name:
        font_face = f"""@font-face {{
  font-family: "NovelEmbeddedFont";
  src: url("../fonts/{embedded_font_name}");
}}
"""
        font_family = '"NovelEmbeddedFont", ' + font_family

    return f"""{font_face}
html, body {{
  margin: 0;
  padding: 0;
}}

body {{
  font-family: {font_family};
  font-size: 1em;
  line-height: 1.75;
  color: #111;
  text-align: justify;
  widows: 2;
  orphans: 2;
}}

h1 {{
  font-family: {font_family};
  font-size: 1.45em;
  line-height: 1.35;
  margin: 2.5em 0 1.5em;
  text-align: center;
  font-weight: bold;
}}

p {{
  font-family: {font_family};
  margin: 0.25em 0;
  text-indent: 2em;
}}
"""


def write_epub(
    output_path: Path,
    title: str,
    author: str,
    chapters: list[Chapter],
    font_family: str,
    font_path: Path | None = None,
) -> None:
    book_id = f"urn:uuid:{uuid.uuid4()}"
    temp_dir = Path(tempfile.mkdtemp(prefix="txt2epub_"))
    try:
        meta_inf = temp_dir / "META-INF"
        oebps = temp_dir / "OEBPS"
        text_dir = oebps / "text"
        styles_dir = oebps / "styles"
        fonts_dir = oebps / "fonts"
        meta_inf.mkdir(parents=True)
        text_dir.mkdir(parents=True)
        styles_dir.mkdir(parents=True)

        embedded_font_name = None
        font_manifest = ""
        if font_path and font_path.exists():
            fonts_dir.mkdir(parents=True)
            embedded_font_name = font_path.name
            shutil.copy2(font_path, fonts_dir / embedded_font_name)
            media_type = font_media_type(font_path)
            font_manifest = (
                f'    <item id="embedded-font" href="fonts/{safe_xml(embedded_font_name)}" '
                f'media-type="{media_type}"/>\n'
            )

        (temp_dir / "mimetype").write_text("application/epub+zip", encoding="ascii")
        (meta_inf / "container.xml").write_text(
            """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
""",
            encoding="utf-8",
        )
        (styles_dir / "style.css").write_text(make_css(font_family, embedded_font_name), encoding="utf-8")

        manifest_items = []
        spine_items = []
        nav_points = []

        for index, chapter in enumerate(chapters, start=1):
            file_name = f"chapter_{index:04d}.xhtml"
            item_id = f"chapter-{index:04d}"
            heading = f"  <h1>{html.escape(chapter.title)}</h1>"
            paragraphs = "\n".join(f"  <p>{html.escape(p)}</p>" for p in chapter.paragraphs)
            (text_dir / file_name).write_text(
                xhtml_page(chapter.title, f"{heading}\n{paragraphs}"),
                encoding="utf-8",
            )
            manifest_items.append(
                f'    <item id="{item_id}" href="text/{file_name}" media-type="application/xhtml+xml"/>'
            )
            spine_items.append(f'    <itemref idref="{item_id}"/>')
            nav_points.append(
                f"""    <navPoint id="navPoint-{index}" playOrder="{index}">
      <navLabel><text>{safe_xml(chapter.title)}</text></navLabel>
      <content src="text/{file_name}"/>
    </navPoint>"""
            )

        nav_links = "\n".join(
            f'    <li><a href="text/chapter_{index:04d}.xhtml">{html.escape(chapter.title)}</a></li>'
            for index, chapter in enumerate(chapters, start=1)
        )
        (oebps / "nav.xhtml").write_text(
            xhtml_page("目录", f"""  <nav epub:type="toc" id="toc" xmlns:epub="http://www.idpf.org/2007/ops">
  <h1>目录</h1>
  <ol>
{nav_links}
  </ol>
  </nav>"""),
            encoding="utf-8",
        )

        (oebps / "toc.ncx").write_text(
            f"""<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head>
    <meta name="dtb:uid" content="{safe_xml(book_id)}"/>
    <meta name="dtb:depth" content="1"/>
    <meta name="dtb:totalPageCount" content="0"/>
    <meta name="dtb:maxPageNumber" content="0"/>
  </head>
  <docTitle><text>{safe_xml(title)}</text></docTitle>
  <docAuthor><text>{safe_xml(author)}</text></docAuthor>
  <navMap>
{chr(10).join(nav_points)}
  </navMap>
</ncx>
""",
            encoding="utf-8",
        )

        (oebps / "content.opf").write_text(
            f"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="BookId" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="BookId">{safe_xml(book_id)}</dc:identifier>
    <dc:title>{safe_xml(title)}</dc:title>
    <dc:creator>{safe_xml(author)}</dc:creator>
    <dc:language>zh-CN</dc:language>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
    <item id="style" href="styles/style.css" media-type="text/css"/>
{font_manifest}{chr(10).join(manifest_items)}
  </manifest>
  <spine toc="ncx">
{chr(10).join(spine_items)}
  </spine>
</package>
""",
            encoding="utf-8",
        )

        if output_path.exists():
            output_path.unlink()
        with zipfile.ZipFile(output_path, "w") as epub:
            epub.write(temp_dir / "mimetype", "mimetype", compress_type=zipfile.ZIP_STORED)
            for file_path in sorted(temp_dir.rglob("*")):
                if file_path.is_file() and file_path.name != "mimetype":
                    arcname = posixpath.join(*file_path.relative_to(temp_dir).parts)
                    epub.write(file_path, arcname, compress_type=zipfile.ZIP_DEFLATED)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def font_media_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".otf":
        return "font/otf"
    if suffix == ".woff":
        return "font/woff"
    if suffix == ".woff2":
        return "font/woff2"
    return "font/ttf"


def kindle_font_css(choice: str) -> str:
    return KINDLE_FONT_MAP.get(choice, KINDLE_FONT_OPTIONS[0][1])


def merge_txt_files(input_paths: list[Path], output_path: Path) -> tuple[int, list[str]]:
    if not input_paths:
        raise ValueError("请先添加要拼接的 TXT 文件")
    if not str(output_path).strip() or output_path.name == "":
        raise ValueError("请先选择拼接后的输出 TXT 文件")

    output_path = output_path.resolve()
    seen_paths: set[Path] = set()
    merged_parts: list[str] = []
    encodings: list[str] = []

    for input_path in input_paths:
        source_path = input_path.resolve()
        if not source_path.exists():
            raise FileNotFoundError(f"TXT 文件不存在：{source_path}")
        if source_path == output_path:
            raise ValueError("输出文件不能和待拼接文件相同")
        if source_path in seen_paths:
            raise ValueError(f"列表中存在重复文件：{source_path}")
        seen_paths.add(source_path)

        text, encoding = read_text(source_path, "auto")
        encodings.append(f"{source_path.name}: {encoding}")
        merged_parts.append(normalize_text(text))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n\n".join(part for part in merged_parts if part), encoding="utf-8")
    return len(input_paths), encodings


class TxtToEpubApp:
    def __init__(self, root: Tk) -> None:
        self.root = root
        self.root.title(APP_NAME)
        self.root.geometry("920x720")
        self.root.minsize(820, 620)
        self.input_path = StringVar(value=str(Path.cwd() / "novel.txt") if (Path.cwd() / "novel.txt").exists() else "")
        self.output_path = StringVar(value=str(Path.cwd() / "novel.epub"))
        self.title = StringVar(value="novel")
        self.author = StringVar(value="未知作者")
        self.encoding = StringVar(value="auto")
        self.target_chars = IntVar(value=500)
        self.use_headings = BooleanVar(value=True)
        self.font_choice = StringVar(value=KINDLE_FONT_OPTIONS[0][0])
        self.font_path = StringVar(value="")
        self.merge_output_path = StringVar(value=str(Path.cwd() / "merged.txt"))
        self.status = StringVar(value="请选择 TXT 文件，然后点击“预览分章”或“开始转换”。")
        self.merge_status = StringVar(value="添加多个 TXT 文件后，可用“上移/下移”指定拼接顺序。")
        self.preview_text = StringVar(value="")
        self.merge_files: list[Path] = []
        self._build_ui()
        if self.input_path.get():
            self._sync_default_title()

    def _build_ui(self) -> None:
        self._configure_styles()
        self.root.configure(background="#f6f1e8")

        shell = ttk.Frame(self.root, style="App.TFrame", padding=(22, 18, 22, 20))
        shell.pack(fill="both", expand=True)

        header = ttk.Frame(shell, style="App.TFrame")
        header.pack(fill="x", pady=(0, 14))
        ttk.Label(header, text="Kindle 小说工具箱", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            header,
            text="自动分章转 EPUB，也可以按指定顺序拼接多个 TXT。",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(4, 0))

        notebook = ttk.Notebook(shell, style="Clean.TNotebook")
        notebook.pack(fill="both", expand=True)
        convert_frame = ttk.Frame(notebook)
        merge_frame = ttk.Frame(notebook)
        notebook.add(convert_frame, text="TXT 转 EPUB")
        notebook.add(merge_frame, text="TXT 拼接")
        self._build_convert_tab(convert_frame)
        self._build_merge_tab(merge_frame)

    def _configure_styles(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except Exception:
            pass

        bg = "#f6f1e8"
        card = "#fffaf2"
        text = "#2f261d"
        muted = "#7c6d5d"
        accent = "#8b4e2f"
        accent_dark = "#6f3c24"
        border = "#ded2c4"

        style.configure(".", font=("Microsoft YaHei UI", 10))
        style.configure("App.TFrame", background=bg)
        style.configure("Card.TFrame", background=card)
        style.configure("Title.TLabel", background=bg, foreground=text, font=("Microsoft YaHei UI", 20, "bold"))
        style.configure("SectionTitle.TLabel", background=card, foreground=text, font=("Microsoft YaHei UI", 12, "bold"))
        style.configure("Muted.TLabel", background=bg, foreground=muted, font=("Microsoft YaHei UI", 10))
        style.configure("CardMuted.TLabel", background=card, foreground=muted, font=("Microsoft YaHei UI", 9))
        style.configure("Field.TLabel", background=card, foreground=text)
        style.configure("Status.TLabel", background=bg, foreground=accent_dark)
        style.configure("CardStatus.TLabel", background=card, foreground=accent_dark)
        style.configure("Card.TLabelframe", background=card, bordercolor=border, relief="solid")
        style.configure("Card.TLabelframe.Label", background=bg, foreground=text, font=("Microsoft YaHei UI", 11, "bold"))
        style.configure("Clean.TNotebook", background=bg, borderwidth=0, tabmargins=(0, 0, 0, 0))
        style.configure("Clean.TNotebook.Tab", padding=(18, 9), font=("Microsoft YaHei UI", 10, "bold"))
        style.map("Clean.TNotebook.Tab", background=[("selected", card)], foreground=[("selected", text)])
        style.configure("Primary.TButton", background=accent, foreground="#ffffff", padding=(16, 8), borderwidth=0)
        style.map("Primary.TButton", background=[("active", accent_dark), ("pressed", accent_dark)])
        style.configure("Tool.TButton", padding=(12, 7))
        style.configure("TEntry", padding=(8, 5))
        style.configure("TCombobox", padding=(8, 5))
        style.configure("Treeview", rowheight=28, font=("Microsoft YaHei UI", 10), background="#fffdf8", fieldbackground="#fffdf8")
        style.configure("Treeview.Heading", font=("Microsoft YaHei UI", 10, "bold"))

    def _make_card(self, parent: ttk.Frame, title: str, row: int, column: int = 0, columnspan: int = 1) -> ttk.LabelFrame:
        card = ttk.LabelFrame(parent, text=title, style="Card.TLabelframe", padding=(16, 12, 16, 14))
        card.grid(row=row, column=column, columnspan=columnspan, sticky="nsew", padx=8, pady=8)
        card.configure(style="Card.TLabelframe")
        return card

    def _path_row(
        self,
        parent: ttk.Frame,
        row: int,
        label: str,
        variable: StringVar,
        button_text: str,
        command,
    ) -> None:
        ttk.Label(parent, text=label, style="Field.TLabel").grid(row=row, column=0, sticky="w", padx=(0, 10), pady=7)
        ttk.Entry(parent, textvariable=variable).grid(row=row, column=1, sticky="ew", pady=7)
        ttk.Button(parent, text=button_text, style="Tool.TButton", command=command).grid(row=row, column=2, sticky="ew", padx=(10, 0), pady=7)
        parent.columnconfigure(1, weight=1)

    def _build_convert_tab(self, frame: ttk.Frame) -> None:
        frame.configure(style="App.TFrame", padding=(0, 10, 0, 0))
        frame.columnconfigure(0, weight=3)
        frame.columnconfigure(1, weight=2)
        frame.rowconfigure(1, weight=1)

        file_card = self._make_card(frame, "文件与书籍信息", 0, 0)
        self._path_row(file_card, 0, "TXT 文件", self.input_path, "选择", self.pick_input)
        self._path_row(file_card, 1, "输出 EPUB", self.output_path, "保存为", self.pick_output)
        ttk.Label(file_card, text="书名", style="Field.TLabel").grid(row=2, column=0, sticky="w", padx=(0, 10), pady=7)
        ttk.Entry(file_card, textvariable=self.title).grid(row=2, column=1, sticky="ew", pady=7)
        ttk.Label(file_card, text="作者", style="Field.TLabel").grid(row=2, column=2, sticky="w", padx=(16, 10), pady=7)
        ttk.Entry(file_card, textvariable=self.author).grid(row=2, column=3, sticky="ew", pady=7)
        ttk.Label(file_card, text="操作", style="Field.TLabel").grid(row=3, column=0, sticky="w", padx=(0, 10), pady=(12, 4))
        top_actions = ttk.Frame(file_card, style="Card.TFrame")
        top_actions.grid(row=3, column=1, columnspan=3, sticky="w", pady=(12, 4))
        ttk.Button(top_actions, text="预览分章", style="Tool.TButton", command=self.preview).pack(side="left", padx=(0, 8))
        ttk.Button(top_actions, text="章节名预览", style="Tool.TButton", command=self.preview_chapter_names).pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(file_card, text="开始转换", style="Primary.TButton", command=self.convert).grid(
            row=4, column=1, sticky="w", pady=(2, 6)
        )
        ttk.Label(file_card, textvariable=self.status, style="CardStatus.TLabel").grid(
            row=5, column=1, columnspan=3, sticky="w", pady=(0, 4)
        )
        file_card.columnconfigure(1, weight=1)
        file_card.columnconfigure(3, weight=1)

        option_card = self._make_card(frame, "转换选项", 0, 1)
        option_card.columnconfigure(1, weight=1)
        option_card.columnconfigure(3, weight=1)
        ttk.Label(option_card, text="编码", style="Field.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 10), pady=7)
        ttk.Combobox(
            option_card,
            textvariable=self.encoding,
            values=("auto", "utf-8", "utf-8-sig", "gb18030", "big5", "utf-16"),
            state="readonly",
            width=18,
        ).grid(row=0, column=1, sticky="w", pady=7)
        ttk.Label(option_card, text="兜底每章字数", style="Field.TLabel").grid(row=0, column=2, sticky="w", padx=(18, 10), pady=7)
        ttk.Spinbox(option_card, from_=100, to=30000, increment=100, textvariable=self.target_chars, width=12).grid(
            row=0, column=3, sticky="w", pady=7
        )
        ttk.Label(option_card, text="Kindle 字体", style="Field.TLabel").grid(row=1, column=0, sticky="w", padx=(0, 10), pady=7)
        ttk.Combobox(
            option_card,
            textvariable=self.font_choice,
            values=tuple(label for label, _ in KINDLE_FONT_OPTIONS),
            state="readonly",
        ).grid(row=1, column=1, columnspan=3, sticky="ew", pady=7)
        ttk.Label(option_card, text="嵌入字体", style="Field.TLabel").grid(row=2, column=0, sticky="w", padx=(0, 10), pady=7)
        ttk.Entry(option_card, textvariable=self.font_path).grid(row=2, column=1, columnspan=2, sticky="ew", pady=7)
        ttk.Button(option_card, text="可选", style="Tool.TButton", command=self.pick_font).grid(
            row=2, column=3, sticky="ew", padx=(10, 0), pady=7
        )
        ttk.Checkbutton(option_card, text="优先识别“第 N 章”等章节标题", variable=self.use_headings).grid(
            row=3, column=1, columnspan=3, sticky="w", pady=(8, 2)
        )
        ttk.Label(
            option_card,
            text="识别不到章节标题时，会按兜底字数自动切分。",
            style="CardMuted.TLabel",
        ).grid(row=4, column=1, columnspan=3, sticky="w", pady=(0, 4))

        preview = self._make_card(frame, "分章预览", 1, 0, 2)
        preview.rowconfigure(2, weight=1)
        preview.columnconfigure(0, weight=1)
        action_bar = ttk.Frame(preview, style="Card.TFrame")
        action_bar.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        ttk.Button(action_bar, text="预览分章", style="Tool.TButton", command=self.preview).pack(side="left", padx=(0, 8))
        ttk.Button(action_bar, text="章节名预览", style="Tool.TButton", command=self.preview_chapter_names).pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(action_bar, text="开始转换", style="Primary.TButton", command=self.convert).pack(side="left")

        self.preview_box = ttk.Treeview(preview, columns=("title", "chars"), show="headings", height=10)
        self.preview_box.heading("title", text="章节")
        self.preview_box.heading("chars", text="字数")
        self.preview_box.column("title", width=520)
        self.preview_box.column("chars", width=120, anchor="e")
        preview_scroll = ttk.Scrollbar(preview, orient="vertical", command=self.preview_box.yview)
        self.preview_box.configure(yscrollcommand=preview_scroll.set)
        self.preview_box.grid(row=2, column=0, sticky="nsew")
        preview_scroll.grid(row=2, column=1, sticky="ns")

    def _build_merge_tab(self, frame: ttk.Frame) -> None:
        frame.configure(style="App.TFrame", padding=(0, 10, 0, 0))
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)

        output_card = self._make_card(frame, "输出设置", 0)
        self._path_row(output_card, 0, "输出 TXT", self.merge_output_path, "保存为", self.pick_merge_output)
        ttk.Label(output_card, text="拼接会按下方列表顺序执行，并统一输出为 UTF-8。", style="CardMuted.TLabel").grid(
            row=1, column=1, sticky="w", pady=(0, 4)
        )

        list_frame = self._make_card(frame, "拼接顺序", 1)
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(1, weight=1)
        button_frame = ttk.Frame(list_frame, style="Card.TFrame")
        button_frame.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        ttk.Button(button_frame, text="添加 TXT", style="Primary.TButton", command=self.add_merge_files).pack(side="left", padx=(0, 8))
        ttk.Button(button_frame, text="移除选中", style="Tool.TButton", command=self.remove_merge_files).pack(side="left", padx=(0, 8))
        ttk.Button(button_frame, text="清空", style="Tool.TButton", command=self.clear_merge_files).pack(side="left", padx=(0, 8))
        ttk.Button(button_frame, text="上移", style="Tool.TButton", command=lambda: self.move_merge_file(-1)).pack(side="left", padx=(0, 8))
        ttk.Button(button_frame, text="下移", style="Tool.TButton", command=lambda: self.move_merge_file(1)).pack(side="left", padx=(0, 8))
        ttk.Button(button_frame, text="开始拼接", style="Primary.TButton", command=self.merge_files_to_txt).pack(side="right")

        self.merge_box = ttk.Treeview(list_frame, columns=("order", "name", "path"), show="headings", height=16)
        self.merge_box.heading("order", text="顺序")
        self.merge_box.heading("name", text="文件名")
        self.merge_box.heading("path", text="完整路径")
        self.merge_box.column("order", width=70, anchor="center")
        self.merge_box.column("name", width=220)
        self.merge_box.column("path", width=480)
        merge_scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.merge_box.yview)
        self.merge_box.configure(yscrollcommand=merge_scroll.set)
        self.merge_box.grid(row=1, column=0, sticky="nsew")
        merge_scroll.grid(row=1, column=1, sticky="ns")

        ttk.Label(frame, textvariable=self.merge_status, style="Status.TLabel").grid(row=2, column=0, sticky="w", padx=8, pady=(4, 0))

    def pick_input(self) -> None:
        path = filedialog.askopenfilename(title="选择 TXT 小说", filetypes=[("Text files", "*.txt"), ("All files", "*.*")])
        if path:
            self.input_path.set(path)
            self.output_path.set(str(Path(path).with_suffix(".epub")))
            self._sync_default_title()

    def pick_output(self) -> None:
        path = filedialog.asksaveasfilename(title="保存 EPUB", defaultextension=".epub", filetypes=[("EPUB", "*.epub")])
        if path:
            self.output_path.set(path)

    def pick_font(self) -> None:
        path = filedialog.askopenfilename(
            title="选择要嵌入的字体文件",
            filetypes=[("Font files", "*.ttf *.otf *.woff *.woff2"), ("All files", "*.*")],
        )
        if path:
            self.font_path.set(path)

    def pick_merge_output(self) -> None:
        path = filedialog.asksaveasfilename(title="保存拼接后的 TXT", defaultextension=".txt", filetypes=[("Text files", "*.txt")])
        if path:
            self.merge_output_path.set(path)

    def add_merge_files(self) -> None:
        paths = filedialog.askopenfilenames(title="选择要拼接的 TXT", filetypes=[("Text files", "*.txt"), ("All files", "*.*")])
        for path in paths:
            file_path = Path(path)
            if file_path not in self.merge_files:
                self.merge_files.append(file_path)
        self.render_merge_files()
        if paths:
            self.merge_status.set(f"已添加 {len(paths)} 个文件；当前共 {len(self.merge_files)} 个。")

    def remove_merge_files(self) -> None:
        selected = self.selected_merge_indices()
        if not selected:
            self.merge_status.set("请先在列表中选中要移除的文件。")
            return
        for index in sorted(selected, reverse=True):
            self.merge_files.pop(index)
        self.render_merge_files()
        self.merge_status.set(f"已移除 {len(selected)} 个文件；当前共 {len(self.merge_files)} 个。")

    def clear_merge_files(self) -> None:
        self.merge_files.clear()
        self.render_merge_files()
        self.merge_status.set("已清空拼接列表。")

    def move_merge_file(self, direction: int) -> None:
        selected = self.selected_merge_indices()
        if len(selected) != 1:
            self.merge_status.set("请选中一个文件后再上移或下移。")
            return
        index = selected[0]
        new_index = index + direction
        if new_index < 0 or new_index >= len(self.merge_files):
            return
        self.merge_files[index], self.merge_files[new_index] = self.merge_files[new_index], self.merge_files[index]
        self.render_merge_files(select_index=new_index)
        self.merge_status.set("已调整拼接顺序。")

    def selected_merge_indices(self) -> list[int]:
        indices: list[int] = []
        for item in self.merge_box.selection():
            values = self.merge_box.item(item, "values")
            if values:
                indices.append(int(values[0]) - 1)
        return sorted(indices)

    def render_merge_files(self, select_index: int | None = None) -> None:
        for item in self.merge_box.get_children():
            self.merge_box.delete(item)
        selected_item = None
        for index, path in enumerate(self.merge_files, start=1):
            item = self.merge_box.insert("", "end", values=(index, path.name, str(path)))
            if select_index is not None and index - 1 == select_index:
                selected_item = item
        if selected_item:
            self.merge_box.selection_set(selected_item)
            self.merge_box.focus(selected_item)

    def merge_files_to_txt(self) -> None:
        try:
            count, encodings = merge_txt_files(self.merge_files, Path(self.merge_output_path.get()))
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc))
            self.merge_status.set("拼接失败，请检查文件列表和输出路径。")
            return
        self.merge_status.set(f"拼接完成：{self.merge_output_path.get()}；共 {count} 个文件，已输出为 UTF-8。")
        messagebox.showinfo(APP_NAME, "TXT 拼接完成：\n" + self.merge_output_path.get() + "\n\n编码识别：\n" + "\n".join(encodings[:20]))

    def _sync_default_title(self) -> None:
        path = Path(self.input_path.get())
        if path.name:
            self.title.set(path.stem)

    def load_and_split(self) -> tuple[list[Chapter], str, str]:
        return self.load_and_split_values(
            input_path=Path(self.input_path.get()),
            encoding_name=self.encoding.get(),
            use_headings=self.use_headings.get(),
            target_chars=self.target_chars.get(),
        )

    def load_and_split_values(
        self,
        input_path: Path,
        encoding_name: str,
        use_headings: bool,
        target_chars: int,
    ) -> tuple[list[Chapter], str, str]:
        if not input_path.exists():
            raise FileNotFoundError("TXT 文件不存在")
        text, encoding = read_text(input_path, encoding_name)
        chapters, mode = build_chapters(text, use_headings, target_chars)
        if not chapters:
            raise ValueError("没有可转换的正文内容")
        return chapters, encoding, mode

    def preview(self) -> None:
        try:
            chapters, encoding, mode = self.load_and_split()
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc))
            return
        self.render_preview(chapters)
        self.status.set(f"预览完成：{len(chapters)} 章，编码 {encoding}，{mode}。")

    def preview_chapter_names(self) -> None:
        try:
            chapters, encoding, mode = self.load_and_split()
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc))
            return

        self.render_preview(chapters)
        window = Toplevel(self.root)
        window.title("章节名预览")
        window.geometry("560x680")
        window.minsize(420, 360)
        window.configure(background="#f6f1e8")

        container = ttk.Frame(window, style="App.TFrame", padding=(18, 16, 18, 16))
        container.pack(fill="both", expand=True)
        ttk.Label(container, text=f"章节名预览：共 {len(chapters)} 章", style="Title.TLabel").pack(anchor="w")
        ttk.Label(container, text=f"编码 {encoding}，{mode}", style="Muted.TLabel").pack(anchor="w", pady=(4, 12))

        text_frame = ttk.Frame(container, style="App.TFrame")
        text_frame.pack(fill="both", expand=True)
        chapter_text = Text(
            text_frame,
            wrap="none",
            font=("Microsoft YaHei UI", 10),
            background="#fffdf8",
            foreground="#2f261d",
            relief="flat",
            padx=12,
            pady=10,
        )
        scrollbar = ttk.Scrollbar(text_frame, orient="vertical", command=chapter_text.yview)
        chapter_text.configure(yscrollcommand=scrollbar.set)
        chapter_text.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        names = "\n".join(f"{index:04d}. {chapter.title}" for index, chapter in enumerate(chapters, start=1))
        chapter_text.insert(END, names)
        chapter_text.configure(state="disabled")
        ttk.Button(container, text="关闭", style="Primary.TButton", command=window.destroy).pack(anchor="e", pady=(12, 0))
        self.status.set(f"章节名预览完成：{len(chapters)} 章。")

    def render_preview(self, chapters: list[Chapter]) -> None:
        for item in self.preview_box.get_children():
            self.preview_box.delete(item)
        for chapter in chapters[:300]:
            chars = sum(len(p) for p in chapter.paragraphs)
            self.preview_box.insert("", "end", values=(chapter.title, chars))
        if len(chapters) > 300:
            self.preview_box.insert("", "end", values=(f"... 其余 {len(chapters) - 300} 章未显示", ""))

    def convert(self) -> None:
        config = {
            "input_path": Path(self.input_path.get()),
            "output_path": Path(self.output_path.get()),
            "title": self.title.get().strip(),
            "author": self.author.get().strip(),
            "encoding_name": self.encoding.get(),
            "use_headings": self.use_headings.get(),
            "target_chars": self.target_chars.get(),
            "font_family": kindle_font_css(self.font_choice.get()),
            "font_path": Path(self.font_path.get()) if self.font_path.get().strip() else None,
        }
        thread = threading.Thread(target=self._convert_worker, kwargs=config, daemon=True)
        thread.start()

    def _convert_worker(
        self,
        input_path: Path,
        output_path: Path,
        title: str,
        author: str,
        encoding_name: str,
        use_headings: bool,
        target_chars: int,
        font_family: str,
        font_path: Path | None,
    ) -> None:
        try:
            self.root.after(0, lambda: self.status.set("正在读取、分章并生成 EPUB..."))
            chapters, encoding, mode = self.load_and_split_values(input_path, encoding_name, use_headings, target_chars)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            write_epub(
                output_path=output_path,
                title=title or output_path.stem,
                author=author or "未知作者",
                chapters=chapters,
                font_family=font_family or "serif",
                font_path=font_path,
            )
            self.root.after(0, lambda: self.render_preview(chapters))
            self.root.after(
                0,
                lambda: self.status.set(f"生成完成：{output_path}；共 {len(chapters)} 章，编码 {encoding}，{mode}。"),
            )
            self.root.after(0, lambda: messagebox.showinfo(APP_NAME, f"EPUB 已生成：\n{output_path}"))
        except Exception as exc:
            error_message = str(exc)
            self.root.after(0, lambda: messagebox.showerror(APP_NAME, error_message))
            self.root.after(0, lambda: self.status.set("生成失败，请检查 TXT 文件、输出路径或字体文件。"))


def main() -> None:
    root = Tk()
    TxtToEpubApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
