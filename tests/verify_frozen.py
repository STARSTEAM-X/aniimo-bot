import ctypes
import os
import subprocess
import sys
import time
from pathlib import Path

project = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project))
sys.path.insert(0, str(project / "tests"))
from test_background import NativeWindow
import windows_backend as backend
from PIL import Image
import pytesseract
from PyInstaller.archive.readers import CArchiveReader


def descendant_pids(root_pid):
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    class PROCESSENTRY32(ctypes.Structure):
        _fields_ = [
            ("dwSize", ctypes.wintypes.DWORD), ("cntUsage", ctypes.wintypes.DWORD),
            ("pid", ctypes.wintypes.DWORD), ("heap", ctypes.c_size_t),
            ("module", ctypes.wintypes.DWORD), ("threads", ctypes.wintypes.DWORD),
            ("parent", ctypes.wintypes.DWORD), ("priority", ctypes.wintypes.LONG),
            ("flags", ctypes.wintypes.DWORD), ("filename", ctypes.wintypes.WCHAR * 260),
        ]
    backend._bind(kernel32, "CreateToolhelp32Snapshot", [ctypes.wintypes.DWORD, ctypes.wintypes.DWORD], ctypes.wintypes.HANDLE)
    backend._bind(kernel32, "Process32FirstW", [ctypes.wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32)], ctypes.wintypes.BOOL)
    backend._bind(kernel32, "Process32NextW", [ctypes.wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32)], ctypes.wintypes.BOOL)
    backend._bind(kernel32, "CloseHandle", [ctypes.wintypes.HANDLE], ctypes.wintypes.BOOL)
    snapshot = kernel32.CreateToolhelp32Snapshot(2, 0)
    parents = {}
    try:
        entry = PROCESSENTRY32()
        entry.dwSize = ctypes.sizeof(entry)
        more = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            parents[entry.pid] = entry.parent
            more = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    result = {root_pid}
    while True:
        children = {pid for pid, parent in parents.items() if parent in result}
        if children.issubset(result):
            return result
        result.update(children)


def main():
    executable = project / "dist" / "AnimoBot_Background_Fix.exe"
    window = NativeWindow()
    try:
        image_path = project / ".tmp" / "frozen_capture.png"
        result = subprocess.run(
            [str(executable), "--capture-probe", str(window.hwnd), str(image_path)],
            timeout=40, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode:
            raise RuntimeError(f"Frozen capture probe exited {result.returncode}")
        image = Image.open(image_path)
        assert image.size == (320, 200)
        assert image.getpixel((20, 20)) == (255, 0, 0)
        assert image.getpixel((20, 180)) == (0, 0, 255)
        print("Frozen capture worker: PASS (real client pixels)")
    finally:
        window.close()

    reader = CArchiveReader(str(executable))
    destination = project / ".tmp" / "bundled_ocr"
    extracted = 0
    for archive_name in reader.toc:
        normalized = archive_name.replace("\\", "/")
        if normalized.startswith("tesseract/"):
            target = destination / Path(normalized).relative_to("tesseract")
            if not target.resolve().is_relative_to(destination.resolve()):
                raise ValueError("Archive path outside test directory")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(reader.extract(archive_name))
            extracted += 1
    pytesseract.pytesseract.tesseract_cmd = str(destination / "tesseract.exe")
    os.environ["TESSDATA_PREFIX"] = str(destination / "tessdata")
    languages = pytesseract.get_languages(config="")
    assert "tha" in languages and "eng" in languages
    print(f"Bundled OCR: PASS ({extracted} files; languages {languages})")

    backend._bind(backend.user32, "GetWindowTextW", [ctypes.wintypes.HWND, ctypes.wintypes.LPWSTR, ctypes.c_int], ctypes.c_int)
    callback_type = ctypes.WINFUNCTYPE(ctypes.wintypes.BOOL, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
    backend._bind(backend.user32, "EnumWindows", [callback_type, ctypes.wintypes.LPARAM], ctypes.wintypes.BOOL)
    backend._bind(backend.user32, "IsWindowVisible", [ctypes.wintypes.HWND], ctypes.wintypes.BOOL)
    process = subprocess.Popen([str(executable)], creationflags=subprocess.CREATE_NO_WINDOW)
    found = []
    owned_pids = {process.pid}

    @callback_type
    def check_window(hwnd, ignored):
        if backend.window_pid(hwnd) in owned_pids and backend.user32.IsWindowVisible(hwnd):
            title = ctypes.create_unicode_buffer(256)
            backend.user32.GetWindowTextW(hwnd, title, len(title))
            if title.value == "Aniimo_Bot":
                found.append(hwnd)
        return True

    try:
        deadline = time.monotonic() + 20
        while not found and time.monotonic() < deadline and process.poll() is None:
            owned_pids.update(descendant_pids(process.pid))
            backend.user32.EnumWindows(check_window, 0)
            time.sleep(0.1)
        if not found:
            raise RuntimeError("Frozen GUI did not initialize")
        backend.user32.PostMessageW(found[0], 0x0010, 0, 0)
        if process.wait(timeout=10) != 0:
            raise RuntimeError("Frozen GUI did not exit cleanly")
        print("Frozen GUI: PASS (initialized and closed)")
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)


if __name__ == "__main__":
    main()
