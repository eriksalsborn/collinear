"""Draws the README banner as an SVG made of shapes only (no fonts for the art), so it looks the same everywhere:
COLLINEAR in big block letters in the app's blue, and Col in his outline exactly as the app draws him."""
from pathlib import Path

BLUE, BLUE_SHADOW, WHITE, MUTED, BACKGROUND = "#5fafff", "#2a5f9e", "#ffffff", "#8a8fa3", "#16161e"  # 256-colour 75 is #5fafff
GREY, GREY_SHADOW = "#a3a8ba", "#4d5163"  # LINEAR is a quieter grey, so COL is what you read first
LETTERS = {
    "C": [".###.", "#...#", "#....", "#....", "#....", "#...#", ".###."],
    "O": [".###.", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."],
    "L": ["#....", "#....", "#....", "#....", "#....", "#....", "#####"],
    "I": ["###", ".#.", ".#.", ".#.", ".#.", ".#.", "###"],
    "N": ["#...#", "##..#", "##..#", "#.#.#", "#..##", "#..##", "#...#"],
    "E": ["#####", "#....", "#....", "####.", "#....", "#....", "#####"],
    "A": [".###.", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"],
    "R": ["####.", "#...#", "#...#", "####.", "#.#..", "#..#.", "#...#"],
}
COL_FACE = ["  ◢▀▄▁▁▁▄▀◣ ", " ▐ ^ ▐▌ ^ ▌ ", "  ▜▖ ▼  ▗▛  ", "   ╰─ω─╯~   "]  # the "cheer" face from issues.py
parts = []


def rect(x, y, width, height, fill, radius=0):
    parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{width:.1f}" height="{height:.1f}" rx="{radius}" fill="{fill}"/>')


def polygon(points, fill):
    parts.append(f'<polygon points="{" ".join(f"{x:.1f},{y:.1f}" for x, y in points)}" fill="{fill}"/>')


def stroke(path, colour, width=2.4):
    parts.append(f'<path d="{path}" fill="none" stroke="{colour}" stroke-width="{width}" stroke-linecap="round" stroke-linejoin="round"/>')


def draw_title(text, left, top, pixel):
    x = left
    for letter_number, letter in enumerate(text):
        rows = LETTERS[letter]
        letter_colour, shadow_colour = (BLUE, BLUE_SHADOW) if letter_number < len("COL") else (GREY, GREY_SHADOW)
        for colour, offset in ((shadow_colour, pixel * 0.28), (letter_colour, 0)):  # a short shadow down-right, then the letter
            for row_number, row in enumerate(rows):
                for column_number, cell in enumerate(row):
                    if cell == "#":
                        rect(x + column_number * pixel + offset, top + row_number * pixel + offset, pixel - 1.5, pixel - 1.5, colour, 2)
        x += (len(rows[0]) + 1.4) * pixel
    return x


def draw_col_glyph(glyph, x, y, w, h):
    half_w, half_h = w / 2, h / 2
    quadrant = {"UL": (x, y), "UR": (x + half_w, y), "LL": (x, y + half_h), "LR": (x + half_w, y + half_h)}
    quadrants_of = {"▀": ("UL", "UR"), "▄": ("LL", "LR"), "▐": ("UR", "LR"), "▌": ("UL", "LL"), "▖": ("LL",), "▗": ("LR",),
                    "▜": ("UL", "UR", "LR"), "▛": ("UL", "UR", "LL")}
    if glyph in quadrants_of:
        for name in quadrants_of[glyph]:
            rect(*quadrant[name], half_w + 0.3, half_h + 0.3, WHITE)
    elif glyph == "▁":
        rect(x, y + h * 7 / 8, w + 0.3, h / 8, WHITE)
    elif glyph == "◢":
        polygon([(x + w, y + h * 0.25), (x + w, y + h), (x, y + h)], WHITE)
    elif glyph == "◣":
        polygon([(x, y + h * 0.25), (x, y + h), (x + w, y + h)], WHITE)
    elif glyph == "▼":
        polygon([(x + w * 0.05, y + h * 0.3), (x + w * 0.95, y + h * 0.3), (x + half_w, y + h * 0.72)], WHITE)
    elif glyph == "^":
        stroke(f"M{x + w * 0.12:.1f} {y + h * 0.5:.1f} L{x + half_w:.1f} {y + h * 0.24:.1f} L{x + w * 0.88:.1f} {y + h * 0.5:.1f}", WHITE, 2.8)
    elif glyph == "─":
        stroke(f"M{x:.1f} {y + half_h:.1f} L{x + w:.1f} {y + half_h:.1f}", WHITE)
    elif glyph == "╰":
        stroke(f"M{x + half_w:.1f} {y:.1f} L{x + half_w:.1f} {y + h * 0.3:.1f} Q{x + half_w:.1f} {y + half_h:.1f} {x + w:.1f} {y + half_h:.1f}", WHITE)
    elif glyph == "╯":
        stroke(f"M{x + half_w:.1f} {y:.1f} L{x + half_w:.1f} {y + h * 0.3:.1f} Q{x + half_w:.1f} {y + half_h:.1f} {x:.1f} {y + half_h:.1f}", WHITE)
    elif glyph == "ω":
        stroke(f"M{x + w * 0.1:.1f} {y + h * 0.36:.1f} Q{x + w * 0.02:.1f} {y + h * 0.66:.1f} {x + w * 0.3:.1f} {y + h * 0.64:.1f} "
               f"Q{x + half_w:.1f} {y + h * 0.62:.1f} {x + half_w:.1f} {y + h * 0.42:.1f} "
               f"Q{x + half_w:.1f} {y + h * 0.62:.1f} {x + w * 0.7:.1f} {y + h * 0.64:.1f} Q{x + w * 0.98:.1f} {y + h * 0.66:.1f} {x + w * 0.9:.1f} {y + h * 0.36:.1f}", WHITE)
    elif glyph == "~":
        stroke(f"M{x + w * 0.05:.1f} {y + h * 0.5:.1f} Q{x + w * 0.3:.1f} {y + h * 0.34:.1f} {x + half_w:.1f} {y + h * 0.5:.1f} "
               f"T{x + w * 0.95:.1f} {y + h * 0.5:.1f}", WHITE)


def draw_col(left, top, cell_width, cell_height):
    for row_number, row in enumerate(COL_FACE):
        for column_number, glyph in enumerate(row):
            draw_col_glyph(glyph, left + column_number * cell_width, top + row_number * cell_height, cell_width, cell_height)
    box_left, box_top = left - cell_width / 2, top - cell_height / 2
    box_width, box_height = (len(COL_FACE[0]) + 1) * cell_width, (len(COL_FACE) + 1) * cell_height
    parts.append(f'<rect x="{box_left:.1f}" y="{box_top:.1f}" width="{box_width:.1f}" height="{box_height:.1f}" rx="12" fill="none" stroke="{BLUE}" stroke-width="2.4"/>')
    tag_width, tag_x = 62, box_left + box_width / 2
    rect(tag_x - tag_width / 2, box_top + box_height - 12, tag_width, 24, BACKGROUND)  # his collar tag sits on the bottom edge
    parts.append(f'<text x="{tag_x:.1f}" y="{box_top + box_height + 6:.1f}" text-anchor="middle" font-family="ui-monospace, SFMono-Regular, Menlo, Consolas, monospace" '
                 f'font-size="18" font-weight="700" fill="{BLUE}">Col</text>')
    return box_left, box_top, box_width, box_height


WIDTH, HEIGHT = 1100, 280
pixel = 12
title_right = draw_title("COLLINEAR", 52, 84, pixel)
box_left, box_top, box_width, box_height = draw_col(title_right + 152, 84, 14, 28)
mono = 'font-family="ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"'
parts.append(f'<text x="{box_left - 14:.1f}" y="{box_top + box_height * 0.45:.1f}" text-anchor="end" {mono} font-size="19" font-weight="700" fill="{BLUE}">“woof” ‹</text>')
parts.append(f'<text x="50" y="{84 + 7 * pixel + 46}" {mono} font-size="20" fill="{MUTED}">Linear issues inside herdr · herded by Col the border collie</text>')
for spark_x, spark_y, size in ((30, 60, 9), (title_right + 6, 60, 9)):  # the two sparks either side of the title, as in the app
    polygon([(spark_x, spark_y - size), (spark_x + size * 0.28, spark_y - size * 0.28), (spark_x + size, spark_y), (spark_x + size * 0.28, spark_y + size * 0.28),
             (spark_x, spark_y + size), (spark_x - size * 0.28, spark_y + size * 0.28), (spark_x - size, spark_y), (spark_x - size * 0.28, spark_y - size * 0.28)], WHITE)
svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {HEIGHT}" width="{WIDTH}" height="{HEIGHT}" role="img" aria-label="Collinear">\n'
       f'<title>Collinear</title>\n<rect width="{WIDTH}" height="{HEIGHT}" rx="18" fill="{BACKGROUND}"/>\n' + "\n".join(parts) + "\n</svg>\n")
out = Path(__file__).with_name("banner.svg")
out.write_text(svg)
print(out, len(svg), "bytes; title ends at x =", round(title_right), "; Col box", round(box_left), "to", round(box_left + box_width))
