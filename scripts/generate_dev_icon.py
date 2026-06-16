#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import tempfile
from pathlib import Path

from AppKit import (
    NSAttributedString,
    NSBezierPath,
    NSBitmapImageRep,
    NSColor,
    NSFont,
    NSFontAttributeName,
    NSForegroundColorAttributeName,
    NSImage,
    NSMakeRect,
    NSMutableParagraphStyle,
    NSParagraphStyleAttributeName,
    NSPNGFileType,
)

ICON_SLOTS = {
    "icon_16x16.png": 16,
    "icon_16x16@2x.png": 32,
    "icon_32x32.png": 32,
    "icon_32x32@2x.png": 64,
    "icon_128x128.png": 128,
    "icon_128x128@2x.png": 256,
    "icon_256x256.png": 256,
    "icon_256x256@2x.png": 512,
    "icon_512x512.png": 512,
    "icon_512x512@2x.png": 1024,
}


def _render_png(path: Path, size: int) -> None:
    image = NSImage.alloc().initWithSize_((size, size))
    image.lockFocus()

    NSColor.colorWithCalibratedRed_green_blue_alpha_(0.78, 0.08, 0.08, 1.0).setFill()
    radius = size * 0.2
    NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(0, 0, size, size),
        radius,
        radius,
    ).fill()

    paragraph = NSMutableParagraphStyle.alloc().init()
    paragraph.setAlignment_(1)  # center
    text_size = max(size * 0.22, 8.0)
    attrs = {
        NSFontAttributeName: NSFont.boldSystemFontOfSize_(text_size),
        NSForegroundColorAttributeName: NSColor.whiteColor(),
        NSParagraphStyleAttributeName: paragraph,
    }
    text = NSAttributedString.alloc().initWithString_attributes_("DEV", attrs)
    text.drawInRect_(NSMakeRect(0, (size - text_size) / 2.3, size, text_size * 1.4))

    image.unlockFocus()
    bitmap = NSBitmapImageRep.imageRepWithData_(image.TIFFRepresentation())
    if bitmap is None:
        raise RuntimeError("Unable to create bitmap representation for icon.")
    png_data = bitmap.representationUsingType_properties_(NSPNGFileType, {})
    if png_data is None:
        raise RuntimeError("Unable to encode PNG data for icon.")
    if not png_data.writeToFile_atomically_(str(path), True):
        raise RuntimeError(f"Unable to write icon PNG: {path}")


def _build_iconset(iconset_dir: Path) -> None:
    iconset_dir.mkdir(parents=True, exist_ok=True)
    for filename, size in ICON_SLOTS.items():
        _render_png(iconset_dir / filename, size)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a red DEV .icns app icon.")
    parser.add_argument(
        "--output",
        required=True,
        help="Absolute path for output .icns file.",
    )
    args = parser.parse_args()

    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="dev-iconset-") as tmp:
        iconset_dir = Path(tmp) / "dev.iconset"
        _build_iconset(iconset_dir)
        subprocess.run(
            ["iconutil", "-c", "icns", str(iconset_dir), "-o", str(output)],
            check=True,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
