import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

project_dir = Path(SPECPATH)
tesseract_dir = Path(os.environ.get("ANIIMO_TESSERACT_DIR", r"C:\Program Files\Tesseract-OCR"))
if not (tesseract_dir / "tesseract.exe").is_file():
    raise SystemExit("Set ANIIMO_TESSERACT_DIR to the folder containing tesseract.exe")

datas = collect_data_files("customtkinter")
datas.append((str(project_dir / "anipod_ui_anchor.png"), "."))
datas.append((str(project_dir / "target_lock_anchor.png"), "."))
for language in ("tha", "eng", "osd"):
    data_path = tesseract_dir / "tessdata" / f"{language}.traineddata"
    if not data_path.is_file():
        raise SystemExit(f"Missing Tesseract language file: {data_path}")
    datas.append((str(data_path), "tesseract/tessdata"))
binaries = [(str(tesseract_dir / "tesseract.exe"), "tesseract")]
binaries.extend((str(path), "tesseract") for path in tesseract_dir.glob("*.dll"))

analysis = Analysis(
    [str(project_dir / "animo_bot.py")], pathex=[str(project_dir)],
    binaries=binaries, datas=datas, hiddenimports=[],
    hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[], noarchive=False,
)
archive = PYZ(analysis.pure)
executable = EXE(
    archive, analysis.scripts, analysis.binaries, analysis.datas, [],
    name="AnimoBot_Background_Fix", debug=False, bootloader_ignore_signals=False,
    strip=False, upx=False, console=False,
)
