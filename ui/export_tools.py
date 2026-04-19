from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from PyQt6.QtWidgets import QApplication, QFileDialog, QWidget

log = logging.getLogger(__name__)


def _default_png_path(prefix: str) -> str:
    default_dir = Path.home() / "Pictures"
    if not default_dir.exists():
        default_dir = Path.home()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return str(default_dir / f"{prefix}_{stamp}.png")


def save_widget_png(widget: QWidget, prefix: str, title: str = "Save PNG") -> str | None:
    """Prompt for a path and save a PNG snapshot of *widget*."""
    pix = widget.grab()
    if pix.isNull():
        log.warning("%s: widget grab returned a null pixmap", prefix)
        return None

    path, _ = QFileDialog.getSaveFileName(
        widget, title, _default_png_path(prefix), "PNG Image (*.png)"
    )
    if not path:
        return None
    if not path.lower().endswith(".png"):
        path += ".png"

    if not pix.save(path, "PNG"):
        log.warning("%s: failed to save PNG to %s", prefix, path)
        return None

    log.info("%s: saved PNG to %s", prefix, path)
    return path


def copy_widget_png(widget: QWidget, prefix: str) -> bool:
    """Copy a PNG-compatible snapshot of *widget* to the system clipboard."""
    pix = widget.grab()
    if pix.isNull():
        log.warning("%s: widget grab returned a null pixmap", prefix)
        return False

    clipboard = QApplication.clipboard()
    clipboard.setPixmap(pix)
    log.info("%s: copied PNG snapshot to clipboard", prefix)
    return True
