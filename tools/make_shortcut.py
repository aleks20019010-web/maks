# tools/make_shortcut.py
# Создаёт assets/icon.ico из assets/icon.(jpg|png) и ярлык «Макс» на Рабочем столе.

import os
import struct
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice, Qt
from PySide6.QtGui import QGuiApplication, QPainter, QPainterPath, QPixmap


BASE_DIR = Path(__file__).resolve().parent.parent
ASSETS_DIR = BASE_DIR / "assets"
ICO_PATH = ASSETS_DIR / "icon.ico"


def _find_source_image() -> Path | None:
    for name in ("icon.jpg", "icon.jpeg", "icon.png"):
        p = ASSETS_DIR / name
        if p.exists():
            return p
    return None


def _rounded_png_bytes(src: QPixmap, size: int) -> bytes:
    scaled = src.scaled(
        size, size,
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation,
    )
    x = (scaled.width() - size) // 2
    y = (scaled.height() - size) // 2
    cropped = scaled.copy(x, y, size, size)

    out = QPixmap(size, size)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    radius = max(3, int(size * 0.24))
    path.addRoundedRect(0, 0, size, size, radius, radius)
    p.setClipPath(path)
    p.drawPixmap(0, 0, cropped)
    p.end()

    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    out.save(buf, "PNG")
    return bytes(buf.data())


def build_ico(src_path: Path, dst_path: Path) -> None:
    app = QGuiApplication.instance() or QGuiApplication(sys.argv)
    src = QPixmap(str(src_path))
    if src.isNull():
        raise RuntimeError(f"Не удалось открыть картинку: {src_path}")

    sizes = [16, 24, 32, 48, 64, 128, 256]
    frames = [(s, _rounded_png_bytes(src, s)) for s in sizes]

    header = struct.pack("<HHH", 0, 1, len(frames))
    dir_entries = bytearray()
    offset = 6 + 16 * len(frames)

    for s, png_data in frames:
        w_byte = 0 if s >= 256 else s
        h_byte = 0 if s >= 256 else s
        dir_entries += struct.pack(
            "<BBBBHHII",
            w_byte, h_byte, 0, 0, 1, 32, len(png_data), offset
        )
        offset += len(png_data)

    dst_path.parent.mkdir(parents=True, exist_ok=True)
    with open(dst_path, "wb") as f:
        f.write(header)
        f.write(dir_entries)
        for _, png_data in frames:
            f.write(png_data)


def create_desktop_shortcut(ico_path: Path | None) -> Path:
    desktop = Path(os.path.expandvars(r"%USERPROFILE%\Desktop"))
    if not desktop.exists():
        # Если рабочий стол перенесён в OneDrive
        ps_desk = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command", "[Environment]::GetFolderPath('Desktop')"],
            text=True,
        ).strip()
        if ps_desk:
            desktop = Path(ps_desk)

    lnk_path = desktop / "Макс.lnk"
    python_exe = Path(sys.executable).resolve()
    icon_arg = f'$s.IconLocation = "{ico_path}";' if ico_path and ico_path.exists() else ""

    ps_script = (
        f'$ws = New-Object -ComObject WScript.Shell; '
        f'$s = $ws.CreateShortcut("{lnk_path}"); '
        f'$s.TargetPath = "{python_exe}"; '
        f'$s.Arguments = "supervisor.py"; '
        f'$s.WorkingDirectory = "{BASE_DIR}"; '
        f'$s.Description = "Макс — голосовой ассистент"; '
        f'{icon_arg} '
        f'$s.Save()'
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", ps_script], check=True)
    return lnk_path


def main():
    src = _find_source_image()
    if src:
        build_ico(src, ICO_PATH)
        print(f"Иконка создана: {ICO_PATH}")
    else:
        print("Картинка assets/icon.jpg не найдена — ярлык будет без своей иконки.")

    lnk = create_desktop_shortcut(ICO_PATH if ICO_PATH.exists() else None)
    print(f"Ярлык создан: {lnk}")


if __name__ == "__main__":
    main()