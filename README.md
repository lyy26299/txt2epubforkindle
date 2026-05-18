# TXT 小说转 EPUB（Kindle）

一个本地小工具：把 TXT 小说转换为 EPUB，并在 TXT 缺少目录/章节元数据时自动分章，方便导入 Kindle 等阅读器。

## 功能

- TXT → EPUB（可填写书名/作者等信息）
- 自动识别/切分章节（支持预览）
- 可选 Kindle 字体（下拉选择，避免手写 CSS 出错）
- TXT 拼接：按顺序合并多个 TXT，自动识别编码并统一输出 UTF-8

## 快速开始

### 直接运行（Windows）

双击运行：`dist\TxtNovelToEpub.exe`

### 用 Python 运行

在项目根目录执行：

```powershell
python .\txt_to_epub_gui.py
```

或双击：`run_converter.bat`

## 使用说明

### TXT 转 EPUB

1. 在“TXT 转 EPUB”页选择输入 TXT
2. 确认输出 EPUB 路径、书名、作者等信息
3. 在“Kindle 字体”下拉框选择字体（可选）
4. 点击“预览分章”检查章节识别结果
5. 点击“开始转换”生成 EPUB

### TXT 拼接

1. 切换到“TXT 拼接”页
2. 点击“添加 TXT”选择多个源文件
3. 选中文件后用“上移/下移”调整顺序
4. 点击“保存为”选择输出 TXT
5. 点击“开始拼接”生成新 TXT（统一 UTF-8）

## 章节识别逻辑（简述）

- 优先识别常见章节行：例如 `第一章 标题` / `第23章 标题` / `Chapter 1`
- 若识别到的章节不足，会按“兜底每章字数”自动切分（默认约 500 字，可在界面调整）

## 重新打包 EXE

项目提供：`build_exe.bat`

修改 `txt_to_epub_gui.py` 后，双击 `build_exe.bat` 重新生成：

```text
dist\TxtNovelToEpub.exe
```

## 项目文件

- `txt_to_epub_gui.py`：GUI 主程序
- `TxtNovelToEpub.spec`：打包配置（PyInstaller）
- `dist\`：已打包的可执行文件
- `build_exe.bat`：一键打包脚本
