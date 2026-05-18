@echo off
cd /d "%~dp0"
python -m PyInstaller --noconfirm --clean --onefile --windowed --name TxtNovelToEpub .\txt_to_epub_gui.py
pause
