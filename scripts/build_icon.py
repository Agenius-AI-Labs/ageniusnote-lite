"""Generate the Voice Notes desktop icon.

Draws an AgeniusDesk-styled microphone glyph on a dark rounded-square,
saves a multi-size .ico at voice_notes_v3/assets/icon.ico.

Run:
    python apps/voice-notes-desktop/scripts/build_icon.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter


# AgeniusDesk-aligned palette (matches the cyberpunk theme tokens).
BG_DARK = (10, 14, 26, 255)        # near-black navy, void
BG_INNER = (16, 22, 40, 255)       # slightly lighter, panel
BORDER = (40, 60, 100, 200)        # subtle electric-blue border
ACCENT = (56, 189, 248, 255)       # cyberpunk accent #38bdf8
ACCENT_BRIGHT = (125, 211, 252, 255)  # accent_hover #7dd3fc
GLOW = (56, 189, 248, 90)          # transparent accent for glow


def make_icon(size: int = 512, accent: tuple = ACCENT, accent_bright: tuple = ACCENT_BRIGHT, glow: tuple = GLOW) -> Image.Image:
    """Render the icon at `size` x `size`. 512 gives us crisp resampling."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # Rounded-square background. Corner radius ~22% of size, Windows 11 vibe.
    pad = int(size * 0.05)
    r = int(size * 0.22)
    box = (pad, pad, size - pad, size - pad)
    d.rounded_rectangle(box, radius=r, fill=BG_DARK)

    # Subtle inner panel highlight, 4px inset, for depth.
    inset = int(size * 0.012)
    inner_box = (box[0] + inset, box[1] + inset, box[2] - inset, box[3] - inset)
    d.rounded_rectangle(inner_box, radius=r - inset, outline=BORDER, width=max(1, int(size * 0.006)))

    cx = size // 2
    cy = size // 2

    # ── Glow layer ────────────────────────────────────────────
    # Render the mic capsule into a separate layer, blur, paste back for glow.
    glow_layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow_layer)
    cap_w = int(size * 0.22)
    cap_h = int(size * 0.36)
    cap_box = (
        cx - cap_w // 2,
        int(cy - cap_h * 0.62),
        cx + cap_w // 2,
        int(cy + cap_h * 0.38),
    )
    gd.rounded_rectangle(cap_box, radius=cap_w // 2, fill=glow)
    glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(radius=size * 0.025))
    img = Image.alpha_composite(img, glow_layer)
    d = ImageDraw.Draw(img)

    # ── Microphone capsule (solid accent) ─────────────────────
    d.rounded_rectangle(cap_box, radius=cap_w // 2, fill=accent)

    # Capsule inner highlight (top half slightly brighter)
    hl_box = (cap_box[0] + int(cap_w * 0.18), cap_box[1] + int(cap_h * 0.08),
              cap_box[2] - int(cap_w * 0.18), cap_box[1] + int(cap_h * 0.42))
    d.rounded_rectangle(hl_box, radius=int(cap_w * 0.18), fill=accent_bright)

    # ── Mic arc (the U-shape under the capsule) ───────────────
    arc_radius = int(size * 0.20)
    arc_box = (cx - arc_radius, cy - int(arc_radius * 0.55),
               cx + arc_radius, cy + int(arc_radius * 1.05))
    arc_width = max(2, int(size * 0.022))
    d.arc(arc_box, start=20, end=160, fill=accent, width=arc_width)

    # ── Stand (short vertical bar) ────────────────────────────
    stand_w = max(2, int(size * 0.022))
    stand_top = int(cy + arc_radius * 0.95)
    stand_bot = int(cy + arc_radius * 1.30)
    d.rounded_rectangle(
        (cx - stand_w // 2, stand_top, cx + stand_w // 2, stand_bot),
        radius=stand_w // 2,
        fill=accent,
    )

    # ── Base (horizontal cap on the stand) ────────────────────
    base_w = int(size * 0.18)
    base_h = max(2, int(size * 0.025))
    base_y = stand_bot
    d.rounded_rectangle(
        (cx - base_w // 2, base_y - base_h // 2, cx + base_w // 2, base_y + base_h // 2),
        radius=base_h // 2,
        fill=accent,
    )

    return img


def make_mic_glyph(size: int = 192, color: tuple = ACCENT) -> Image.Image:
    """Standalone microphone glyph on a transparent background.

    Used as a QIcon on mic buttons in capture_panel + quick_note_panel.
    Drawn so the bounding box is roughly centered with even padding.
    """
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    cx = size // 2
    cy = int(size * 0.46)

    # Capsule (mic body)
    cap_w = int(size * 0.40)
    cap_h = int(size * 0.62)
    cap_box = (cx - cap_w // 2, cy - cap_h // 2, cx + cap_w // 2, cy + cap_h // 2)
    d.rounded_rectangle(cap_box, radius=cap_w // 2, fill=color)

    # Arc (U under the capsule)
    arc_radius = int(size * 0.32)
    arc_cy = cy + int(cap_h * 0.20)
    arc_box = (cx - arc_radius, arc_cy - int(arc_radius * 0.6),
               cx + arc_radius, arc_cy + int(arc_radius * 1.0))
    arc_width = max(3, int(size * 0.05))
    d.arc(arc_box, start=20, end=160, fill=color, width=arc_width)

    # Stand
    stand_w = max(3, int(size * 0.05))
    stand_top = arc_cy + int(arc_radius * 0.90)
    stand_bot = stand_top + int(size * 0.10)
    d.rounded_rectangle(
        (cx - stand_w // 2, stand_top, cx + stand_w // 2, stand_bot),
        radius=stand_w // 2,
        fill=color,
    )

    # Base
    base_w = int(size * 0.30)
    base_h = max(3, int(size * 0.05))
    d.rounded_rectangle(
        (cx - base_w // 2, stand_bot - base_h // 2,
         cx + base_w // 2, stand_bot + base_h // 2),
        radius=base_h // 2,
        fill=color,
    )

    return img


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    out_dir = repo_root / "voice_notes_v3" / "assets"
    out_dir.mkdir(parents=True, exist_ok=True)

    big = make_icon(512)
    png_path = out_dir / "icon.png"
    big.save(png_path)

    # Multi-size .ico for Windows shell.
    sizes = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]
    ico_path = out_dir / "icon.ico"
    big.save(ico_path, format="ICO", sizes=sizes)

    # Mic-button variants. Same rounded-square + glowing-mic look as the
    # desktop icon, generated at 192px for crisp downsampling onto a 56px button.
    # Idle = cyan accent. Recording = red accent.
    btn_idle = make_icon(192)
    btn_idle_path = out_dir / "mic_button.png"
    btn_idle.save(btn_idle_path)

    rec_accent = (248, 113, 113, 255)        # red
    rec_bright = (252, 165, 165, 255)        # lighter red highlight
    rec_glow = (248, 113, 113, 90)
    btn_rec = make_icon(192, accent=rec_accent, accent_bright=rec_bright, glow=rec_glow)
    btn_rec_path = out_dir / "mic_button_recording.png"
    btn_rec.save(btn_rec_path)

    # Legacy mic glyphs (transparent bg) kept for any other callers.
    mic_white = make_mic_glyph(192, color=(245, 250, 255, 255))
    mic_white.save(out_dir / "mic.png")
    mic_red = make_mic_glyph(192, color=rec_accent)
    mic_red.save(out_dir / "mic_recording.png")

    print(f"Wrote {png_path}")
    print(f"Wrote {ico_path}")
    print(f"Wrote {btn_idle_path}")
    print(f"Wrote {btn_rec_path}")


if __name__ == "__main__":
    main()
