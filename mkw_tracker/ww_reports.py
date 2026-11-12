"""WW-only PNG reports using the same observed-rating summary as the dashboard."""

from io import BytesIO
from math import ceil, floor, log10

from .report_fonts import draw_text, FontLibrary, text_clusters
from .reports import _check_font_text
from .themes import resolve_theme
from .ww_store import ww_summary

MAX_REPORT_RACES = 200
MAX_REPORT_HEIGHT = 16000
REPORT_WIDTH = 1200


def ww_folder_report(snapshot, folder_id):
    """Select a folder's own label and latest revisions, not active-session data."""
    if snapshot.get("tracker_mode") != "ww":
        raise ValueError("WW reports require a WW session")
    folder = next((folder for folder in snapshot["folders"] if folder["id"] == folder_id), None)
    if folder is None:
        raise KeyError(folder_id)
    races = [race for race in snapshot["races"] if race["folder_id"] == folder_id]
    name = snapshot["ww_player_names"].get(folder["session_id"], "Local player")
    return {"folder": dict(folder), "races": races, **ww_summary(races, name)}


def vr_scaled_bounds(values):
    """Nice integer VR ticks near the observed range, without forcing zero."""
    known = [value for value in values if value is not None]
    if not known:
        return 0, 100, [0, 20, 40, 60, 80, 100]
    low, high = min(known), max(known)
    padding = max(5, (high - low) * .10)
    desired = (high - low + padding * 2) / 5
    magnitude = 10 ** floor(log10(desired))
    step = max(1, ceil(next(multiplier * magnitude for multiplier in (1, 2, 2.5, 5, 10)
                            if multiplier * magnitude >= desired)))
    lower = max(0, floor((low - padding) / step) * step)
    upper = ceil((high + padding) / step) * step
    return lower, upper, list(range(lower, upper + step, step))


def vr_graph_segments(graph):
    """Return indexed known points, breaking on unknown or discontinuous input."""
    segments, current = [], []
    for index, point in enumerate(graph):
        if point["vr"] is None or point["gap_before"]:
            if current:
                segments.append(current)
                current = []
        if point["vr"] is not None:
            current.append((index, point["vr"]))
    if current:
        segments.append(current)
    return segments


def _wrapped_lines(value, font, width):
    lines, line = [], ""
    for character in text_clusters(str(value)):
        if line and font.getlength(line + character) > width:
            lines.append(line)
            line = ""
        line += character
    lines.append(line)
    return lines


def render_ww_folder_png(snapshot, folder_id, theme="mario", *, font_library=None, report_font="",
                         course_images=None):
    """Render personal VR, graph, race history and course averages as PNG bytes."""
    report = ww_folder_report(snapshot, folder_id)
    course_images = course_images or {}
    if len(report["races"]) > MAX_REPORT_RACES:
        raise ValueError(f"WW PNG export supports at most {MAX_REPORT_RACES} races in one session")
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as error:
        raise RuntimeError("PNG export requires Pillow; install the project's reports extra.") from error
    selected_theme = resolve_theme(theme)
    tokens = selected_theme["tokens"]
    fonts = font_library or FontLibrary()
    def font_at(size):
        return fonts.font(ImageFont, size, report_font)

    player_font = font_at(25)
    font = font_at(20)
    small = font_at(16)
    heading = font_at(28)
    large = font_at(36)
    operations = []

    def text(value, x, y, width, selected=font, color=None):
        _check_font_text(selected, value)
        for line in _wrapped_lines(value, selected, width):
            operations.append(("text", (x, y), line, selected, color or tokens["text"]))
            y += selected.size + 7
        return y

    def rectangle(bounds, color, radius=0):
        operations.append(("rectangle", bounds, color, radius))

    def line(points, color, width=1):
        operations.append(("line", points, color, width))

    def known(value):
        return "Unknown" if value is None else str(value)

    def course_label(course, label, top, width):
        png = course_images.get(course)
        if not png:
            return text(label, 48, top + 4, width)
        operations.append(('course-icon', (48, int(top + 4)), png))
        return max(top + 48, text(label, 132, top + 4, width - 84))

    rectangle((0, 0, REPORT_WIDTH, 10), tokens["accent"])
    portrait_png = selected_theme.get("icon_png")
    title_x = 120 if portrait_png else 32
    if portrait_png:
        operations.append(("portrait", (32, 24), portrait_png))
    y = text("WW SESSION", title_x, 30, 1168 - title_x, heading, tokens["accent"])
    y = text(report["folder"]["name"], title_x, y + 4, 1168 - title_x, large)
    if portrait_png:
        y = max(y, 104)
    y = text(report["player_name"], 32, y + 5, 1136, player_font)
    y = text(f"{report['recorded_races']} recorded  /  {report['pending_races']} pending", 32, y + 8,
             1136, small, tokens["muted"]) + 14
    for index, (label, key) in enumerate((('Current VR', 'current_vr'), ('Peak VR', 'peak_vr'),
                                         ('Lowest VR', 'lowest_vr'), ('Starting VR', 'starting_vr'))):
        x = 32 + index * 288
        rectangle((x, y, x + 272, y + 108), tokens["panel"], 12)
        text(label, x + 16, y + 12, 240, small, tokens["muted"])
        text(known(report[key]), x + 16, y + 43, 240, large, tokens["accent"])
    y += 132
    y = text("Observed VR by race", 32, y, 1136, heading)
    graph = report["graph"]
    left, right, top, bottom = 108, REPORT_WIDTH - 44, y + 20, y + 290
    lower, upper, ticks = vr_scaled_bounds(point["vr"] for point in graph)

    def point(index, vr):
        x = (left + right) / 2 if len(graph) < 2 else left + index * (right - left) / (len(graph) - 1)
        return x, bottom - (vr - lower) * (bottom - top) / (upper - lower)

    if any(entry["vr"] is not None for entry in graph):
        for tick in ticks:
            ordinate = point(0, tick)[1]
            line([(left, ordinate), (right, ordinate)], tokens["line"])
            text(tick, 32, ordinate - 12, 68, small, tokens["muted"])
        for segment in vr_graph_segments(graph):
            coordinates = [point(index, vr) for index, vr in segment]
            if len(coordinates) > 1:
                line(coordinates, tokens["accent"], 4)
            for x, ordinate in coordinates:
                operations.append(("dot", (x - 4, ordinate - 4, x + 4, ordinate + 4), tokens["accent"]))
        stride = max(1, ceil(len(graph) / 12))
        for index, entry in enumerate(graph):
            if index % stride and index != len(graph) - 1:
                continue
            number = index if graph[0]["race_id"] is None else index + 1
            label = "Start" if entry["race_id"] is None else f"R{number}"
            x = point(index, lower)[0]
            text(label, x - small.getlength(label) / 2, bottom + 12, 80, small, tokens["muted"])
    else:
        rectangle((left, top, right, bottom), tokens["panel"], 12)
        text("No known VR readings yet", left + 24, top + 105, right - left - 48,
             heading, tokens["muted"])
    y = bottom + 64
    y = text("Race results", 32, y, 1136, heading) + 10
    columns = (("Race / course", 48, 610), ("Place", 680, 100),
               ("VR change", 820, 150), ("Final VR", 1000, 150))

    def table_header(columns, top):
        rectangle((32, top, 1168, top + 38), tokens["panel"], 6)
        for label, x, width in columns:
            text(label, x, top + 5, width, small, tokens["muted"])
        return top + 46

    y = table_header(columns, y)
    if not report["races"]:
        y = text("No races in this session.", 48, y, 1088, font, tokens["muted"]) + 14
    for index, race in enumerate(report["races"], 1):
        result = race["ww_result"]
        bottom_row = course_label(race['course'], f"R{index}  {race['course']}", y, 610)
        for value, x, width in ((result["position"] if result else "Pending", 680, 100),
                                 (f"{result['delta']:+d}" if result and result["delta"] is not None else None,
                                  820, 150), (result["vr_after"] if result else None, 1000, 150)):
            bottom_row = max(bottom_row, text(known(value), x, y + 4, width, font,
                                              tokens["text"] if result else tokens["muted"]))
        y = bottom_row + 12
        line([(48, y), (1152, y)], tokens["line"])
        y += 12
    y = text("Best courses by mean position", 32, y + 20, 1136, heading) + 12
    y = table_header((("Course", 48, 495), ("Mean position", 560, 150),
                      ("Mean VR change", 760, 165), ("Samples", 980, 155)), y)
    if not report["courses"]:
        y = text("No recorded results yet.", 48, y, 1088, font, tokens["muted"])
    for course in report["courses"]:
        bottom_row = course_label(course["course"], course["course"], y, 495)
        average = course["average_change"]
        for value, x, width in ((f"{course['average_position']:.2f}", 560, 150),
                                 (f"{average:+.2f}" if average is not None else "Unknown", 760, 165),
                                 (f"{course['known_changes']} / {course['races']}", 980, 155)):
            bottom_row = max(bottom_row, text(value, x, y + 4, width))
        y = bottom_row + 12
        line([(48, y), (1152, y)], tokens["line"])
        y += 12
    height = ceil(y + 28)
    if height > MAX_REPORT_HEIGHT:
        raise ValueError(f"WW PNG is too tall; the maximum height is {MAX_REPORT_HEIGHT} pixels")
    image = Image.new("RGB", (REPORT_WIDTH, height), tokens["bg"])
    draw = ImageDraw.Draw(image)
    for operation in operations:
        kind, *args = operation
        if kind == "text":
            position, label, selected, color = args
            draw_text(draw, position, label, font=selected, fill=color)
        elif kind == "rectangle":
            bounds, color, radius = args
            draw.rounded_rectangle(bounds, radius=radius, fill=color)
        elif kind == "line":
            points, color, width = args
            draw.line(points, fill=color, width=width)
        elif kind == "dot":
            bounds, color = args
            draw.ellipse(bounds, fill=color)
        elif kind == 'course-icon':
            (x, top), png = args
            with Image.open(BytesIO(png)) as original:
                icon = original.convert('RGBA')
                icon.thumbnail((72, 44))
            image.paste(icon, (x + (72 - icon.width) // 2, top + (44 - icon.height) // 2), icon)
        elif kind == "portrait":
            (x, top), png = args
            with Image.open(BytesIO(png)) as original:
                icon = original.convert("RGBA")
                icon.thumbnail((72, 72))
            image.paste(icon, (x + (72 - icon.width) // 2, top + (72 - icon.height) // 2), icon)
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()
