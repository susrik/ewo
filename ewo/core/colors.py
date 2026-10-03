"""Color palette and helpers for the GUI.

The palette is based on Paul Tol's colour schemes ("Tol"): the 12-color
*muted* set plus the 7-color *vibrant* set, both colour-blind safe. See
https://personal.sron.nl/~pault/ . The priority and status colours live in
``ewo/server/static/theme.css`` (Tol-cited) so daisyUI is recoloured from one
place; the label swatches below are the single Python source for the picker
grid, extending the base palette with a light tint row to give enough distinct
swatches (>25) without overwhelming the chooser.
"""

from __future__ import annotations

# Tol vibrant (7) + Tol muted (12), in a deterministic, categorical order.
_BASE = [
    "#0077BB",  # vibrant blue
    "#33BBEE",  # vibrant cyan
    "#009988",  # vibrant teal
    "#EE7733",  # vibrant orange
    "#CC3311",  # vibrant red
    "#EE3377",  # vibrant magenta
    "#BBBBBB",  # vibrant grey
    "#332288",  # muted indigo
    "#117733",  # muted green
    "#44AA99",  # muted teal
    "#88CCEE",  # muted light cyan
    "#DDCC77",  # muted sand
    "#CC6677",  # muted rose
    "#AA4499",  # muted purple
    "#882255",  # muted wine
    "#661100",  # muted dark brown
    "#6699CC",  # muted blue-grey
    "#999933",  # muted olive
    "#888888",  # muted grey
]


def _lighten(hex_color: str, factor: float = 0.7) -> str:
    """Mix a hex color toward white by ``factor`` (1.0 = white)."""
    r = int(hex_color[1:3], 16)
    g = int(hex_color[3:5], 16)
    b = int(hex_color[5:7], 16)
    r = round(r + (255 - r) * factor)
    g = round(g + (255 - g) * factor)
    b = round(b + (255 - b) * factor)
    return f"#{r:02X}{g:02X}{b:02X}"


def label_palette() -> list[str]:
    """The swatch order for the label color picker: every base color followed
    by its lightened variant, giving a compact two-row grid."""
    palette: list[str] = []
    for color in _BASE:
        palette.append(color)
    for color in _BASE:
        palette.append(_lighten(color))
    return palette


def label_text_color(color: str) -> str:
    """Black or white text for a background hex, by relative luminance."""
    r = int(color[1:3], 16)
    g = int(color[3:5], 16)
    b = int(color[5:7], 16)
    luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255
    return "#000000" if luminance > 0.55 else "#ffffff"


def label_swatch_to_css(color: str) -> str:
    """A background/fg pair as an inline ``style`` value for a pillbox."""
    return f"background-color: {color}; color: {label_text_color(color)};"
