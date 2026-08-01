"""Synthesize POV frames to test the vision path without a camera.

Three frames covering what the demo actually needs to handle:

    monitor.jpg   a vitals monitor with legible numbers — the case for reading
                  a value and asking the room to confirm it
    blurred.jpg   the same monitor, out of focus — must NOT produce confident
                  readings; this is the frame that proves it declines
    empty.jpg     a wall — must produce nothing at all rather than inventing

    ./venv/bin/python -m tests.make_test_frames
"""

import os

from PIL import Image, ImageDraw, ImageFilter, ImageFont

OUT_DIR = "/tmp/oncall-frames"
SIZE = (960, 640)


def _font(size: int):
    for path in (
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ):
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                pass
    return ImageFont.load_default()


def monitor_frame() -> Image.Image:
    """A patient monitor as it looks from across a bed."""
    img = Image.new("RGB", SIZE, (12, 14, 18))
    d = ImageDraw.Draw(img)

    # bezel
    d.rounded_rectangle([90, 60, 870, 580], radius=18, fill=(6, 8, 11), outline=(48, 54, 64), width=6)

    rows = [
        ("HR", "118", (60, 220, 120), 120),
        ("NIBP", "98/62", (240, 240, 250), 250),
        ("SpO2", "95", (120, 190, 255), 380),
        ("RR", "24", (250, 210, 90), 490),
    ]
    for label, value, colour, y in rows:
        d.text((130, y - 28), label, font=_font(30), fill=(150, 160, 175))
        d.text((300, y - 46), value, font=_font(76), fill=colour)

    # a rhythm trace so it reads as a monitor, not a spreadsheet
    pts = []
    for x in range(600, 850, 4):
        t = (x - 600) % 60
        y = 150 - (70 if t == 20 else -25 if t == 26 else 0)
        pts.append((x, y))
    d.line(pts, fill=(60, 220, 120), width=3)

    return img


def empty_frame() -> Image.Image:
    img = Image.new("RGB", SIZE, (78, 80, 86))
    d = ImageDraw.Draw(img)
    for y in range(0, SIZE[1], 40):
        d.line([(0, y), (SIZE[0], y)], fill=(72, 74, 80), width=1)
    return img


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)

    monitor = monitor_frame()
    monitor.save(f"{OUT_DIR}/monitor.jpg", quality=88)

    # Heavy blur: the model must decline rather than guess digits.
    monitor.filter(ImageFilter.GaussianBlur(9)).save(f"{OUT_DIR}/blurred.jpg", quality=88)

    empty_frame().save(f"{OUT_DIR}/empty.jpg", quality=88)

    for name in ("monitor", "blurred", "empty"):
        path = f"{OUT_DIR}/{name}.jpg"
        print(f"  {path}  {os.path.getsize(path):,} bytes")


if __name__ == "__main__":
    main()
