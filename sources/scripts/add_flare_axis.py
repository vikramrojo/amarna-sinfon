"""Generate sources/Amarna.glyphs from sources/amarna.glyphspackage.

Adds a Flare (FLAR, 0-100) axis: every master gets a flared counterpart in
which stem ends swell outward along a smooth curve. The package is only read,
never written, so this can be re-run after tuning the constants.

Each flared stroke end is then checked (bend, symmetry, metric lines, nubs)
and scaled back until it passes.

Usage: python scripts/add_flare_axis.py [--report]   (from the sources directory)
"""

import argparse
import copy
import math
import string
from pathlib import Path

import openstep_plist
import pathops

SOURCES = Path(__file__).resolve().parent.parent
PACKAGE = SOURCES / "amarna.glyphspackage"
OUTPUT = SOURCES / "Amarna.glyphs"

AXIS_NAME = "Flare"
AXIS_TAG = "FLAR"
AXIS_MAX = 100
ID_SUFFIX = "F"
NAME_SUFFIX = " Flared"

# How far each corner of a stroke end moves out, as a fraction of the end's width.
TERMINAL_OUT = 0.08
# Length of stem over which the flare curves in, in end widths.
FLARE_LENGTH = 2.2
# A side only this much longer than the flare is curved whole instead of being split.
SPLIT_MARGIN = 1.1

# A terminal is a straight edge this many reference stems long...
TERMINAL_MIN = 0.45
TERMINAL_MAX = 1.9
# ...whose corners both turn left (ink is on the left of every contour)...
CORNER_MIN = 40
CORNER_MAX = 140
# ...onto sides that run back roughly parallel to each other.
PARALLEL_TOLERANCE = 25
# A stroke end is buried if another contour covers a point this far inside it.
BURIED_PROBE = 4
# A corner is joined to another contour, and left alone, if that contour covers a point
# this many reference stems inside the stroke and just past where the corner would move to.
JOIN_PROBE = 0.15

# Checks. A stroke end that fails one is scaled back by REDUCE_STEP until it passes,
# and dropped once it falls below MIN_SCALE.
MAX_BEND = 8  # degrees a flared side may turn away from its stem
SYMMETRY_TOLERANCE = 2  # degrees the two sides of one end may differ by
NUB_MAX = 10  # merged-outline edges shorter than this, that the flare created, are nubs
NUB_MIN = 1.5
NUB_REACH = 60  # a nub is blamed on the nearest flared corner within this distance
REDUCE_STEP = 0.75
MIN_SCALE = 0.2

REFERENCE_STEM_GLYPH = "I"
WEIGHT_AXIS_INDEX = 0
ITALIC_AXIS_INDEX = 1

PILOT_GLYPHS = set(string.ascii_uppercase) | set(string.ascii_lowercase) | {
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
}
# Glyphs the heuristic gets wrong; they keep their unflared outlines.
SKIP_GLYPHS = set()


def load(path):
    with open(path, encoding="utf-8") as fp:
        return openstep_plist.load(fp, use_numbers=True)


def is_offcurve(node):
    return node[2][0] == "o"


def is_line(node):
    return node[2][0] == "l"


def add(a, b):
    return (a[0] + b[0], a[1] + b[1])


def sub(a, b):
    return (a[0] - b[0], a[1] - b[1])


def lerp(a, b, t):
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def length(v):
    return math.hypot(v[0], v[1])


def flare_direction(edge):
    """The axis a stroke end widens along: sloped ends widen sideways, so their corners keep their height."""
    if abs(edge[0]) >= abs(edge[1]):
        return (edge[0], 0)
    return (0, edge[1])


def turn(v1, v2):
    """Signed angle in degrees from v1 to v2; left turns are positive."""
    return math.degrees(math.atan2(v1[0] * v2[1] - v1[1] * v2[0], v1[0] * v2[0] + v1[1] * v2[1]))


def draw(nodes, pen):
    """Draw a closed path's nodes with a segment pen."""
    count = len(nodes)
    start = next(i for i in range(count) if not is_offcurve(nodes[i]))
    pen.moveTo(tuple(nodes[start][:2]))
    handles = []
    for k in range(1, count + 1):
        node = nodes[(start + k) % count]
        if is_offcurve(node):
            handles.append(tuple(node[:2]))
        elif handles:
            pen.curveTo(*handles, tuple(node[:2]))
            handles = []
        elif k < count:
            pen.lineTo(tuple(node[:2]))
    pen.closePath()


def flatten(nodes, steps=8):
    """Polygon approximation of a closed path."""
    count = len(nodes)
    start = next(i for i in range(count) if not is_offcurve(nodes[i]))
    points = [tuple(nodes[start][:2])]
    handles = []
    for k in range(1, count + 1):
        node = nodes[(start + k) % count]
        if is_offcurve(node):
            handles.append(tuple(node[:2]))
            continue
        end = tuple(node[:2])
        if len(handles) == 2:
            p0, (p1, p2) = points[-1], handles
            for s in range(1, steps + 1):
                t = s / steps
                a, b, c, d = (1 - t) ** 3, 3 * t * (1 - t) ** 2, 3 * t * t * (1 - t), t ** 3
                points.append((
                    a * p0[0] + b * p1[0] + c * p2[0] + d * end[0],
                    a * p0[1] + b * p1[1] + c * p2[1] + d * end[1],
                ))
        else:
            points.append(end)
        handles = []
    return points


def inside(point, polygon):
    x, y = point
    result = False
    for i in range(len(polygon)):
        (x1, y1), (x2, y2) = polygon[i], polygon[i - 1]
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            result = not result
    return result


def find_terminals(nodes, stem, metric_lines, other_polygons):
    """Stroke ends A->B as dicts giving the corner indices and how far each moves (0, 1 or 2 shares)."""
    count = len(nodes)
    terminals = []
    for ib in range(count):
        ia = (ib - 1) % count
        a, b = nodes[ia], nodes[ib]
        if not is_line(b) or is_offcurve(a):
            continue
        edge = sub(b, a)
        if not TERMINAL_MIN * stem <= length(edge) <= TERMINAL_MAX * stem:
            continue
        before = sub(a, nodes[(ia - 1) % count])
        after = sub(nodes[(ib + 1) % count], b)
        if not length(before) or not length(after):
            continue
        turn_a, turn_b = turn(before, edge), turn(edge, after)
        if not (CORNER_MIN <= turn_a <= CORNER_MAX and CORNER_MIN <= turn_b <= CORNER_MAX):
            continue
        if abs(turn_a + turn_b - 180) > PARALLEL_TOLERANCE:
            continue
        width = length(edge)
        unit = (edge[0] / width, edge[1] / width)
        middle = (
            (a[0] + b[0]) / 2 - unit[1] * BURIED_PROBE,
            (a[1] + b[1]) / 2 + unit[0] * BURIED_PROBE,
        )
        if any(inside(middle, polygon) for polygon in other_polygons):
            continue
        out = TERMINAL_OUT * width
        depth = JOIN_PROBE * stem
        shares = []
        for corner, sign in ((a, -1), (b, 1)):
            probe = (
                corner[0] + sign * unit[0] * (out + 2) - unit[1] * depth,
                corner[1] + sign * unit[1] * (out + 2) + unit[0] * depth,
            )
            shares.append(0 if any(inside(probe, polygon) for polygon in other_polygons) else 1)
        # A corner on a metric line stays on it; the opposite corner takes its share of the flare.
        upright = flare_direction(edge)[0] == 0
        locked = [upright and node[1] in metric_lines for node in (a, b)]
        if all(locked):
            continue
        if locked[0]:
            shares = [0, shares[1] * 2]
        elif locked[1]:
            shares = [shares[0] * 2, 0]
        if any(shares):
            terminals.append({"a": ia, "b": ib, "shares": shares})
    # A corner shared by two terminals belongs to a dot or block, not a stroke.
    uses = {}
    for terminal in terminals:
        for index in (terminal["a"], terminal["b"]):
            uses[index] = uses.get(index, 0) + 1
    return [t for t in terminals if uses[t["a"]] == 1 and uses[t["b"]] == 1]


def plan_path(nodes, stem, metric_lines, other_polygons):
    """Decide, from one master, which stroke ends flare and how their sides are split into curves.

    The plan refers to nodes by index only, so it applies to every compatible master.
    Returns (terminals, jobs): jobs maps the end index of a straight side to
    {"start": (terminal, split), "end": (terminal, split)}, where split is the fraction of
    the side, measured from the flaring corner, at which a node is inserted (None: no split).
    """
    count = len(nodes)
    terminals = find_terminals(nodes, stem, metric_lines, other_polygons)
    jobs = {}
    for number, terminal in enumerate(terminals):
        width = length(sub(nodes[terminal["b"]], nodes[terminal["a"]]))
        sides = []
        for corner, step, share in (
            (terminal["a"], -1, terminal["shares"][0]),
            (terminal["b"], 1, terminal["shares"][1]),
        ):
            if not share:
                continue
            neighbour = (corner + step) % count
            side_end = corner if step == -1 else neighbour
            if is_offcurve(nodes[neighbour]) or not is_line(nodes[side_end]):
                continue
            side_length = length(sub(nodes[neighbour], nodes[corner]))
            sides.append((side_end, "end" if step == -1 else "start", side_length))
        if not sides:
            continue
        # Both sides of one end flare over the same length, so the end stays symmetric.
        reach = min([FLARE_LENGTH * width] + [side_length for _, _, side_length in sides])
        for side_end, role, side_length in sides:
            split = reach / side_length if side_length > reach * SPLIT_MARGIN else None
            jobs.setdefault(side_end, {})[role] = (number, split)
    return terminals, jobs


def quadratic(start, control, end):
    """Cubic handles for the curve from start to end that a single quadratic control would give."""
    return [lerp(start, control, 2 / 3), lerp(end, control, 2 / 3)]


def build_path(nodes, terminals, jobs, scales):
    """Rebuild a path with its planned sides as curves; scales gives each terminal's flare (0: none).

    Returns (new nodes, flared corners as (terminal, old position, new position),
    sides as (terminal, bend in degrees)).
    With every scale at 0 the outline is unchanged, only restructured.
    """
    count = len(nodes)
    moves = {}
    corners = []
    for number, terminal in enumerate(terminals):
        along = flare_direction(sub(nodes[terminal["b"]], nodes[terminal["a"]]))
        for corner, sign, step, share in (
            (terminal["a"], -1, -1, terminal["shares"][0]),
            (terminal["b"], 1, 1, terminal["shares"][1]),
        ):
            out = sign * TERMINAL_OUT * share * scales[number]
            if not out:
                continue
            moves[corner] = (along[0] * out, along[1] * out)
            corners.append((number, tuple(nodes[corner][:2]), add(nodes[corner][:2], moves[corner])))
            # A curve handle next to the corner travels with it.
            neighbour = (corner + step) % count
            if is_offcurve(nodes[neighbour]):
                moves[neighbour] = moves[corner]

    def moved(index):
        return add(nodes[index][:2], moves.get(index, (0, 0)))

    bends = []

    def bend(number, stem_from, stem_to, handle, corner):
        if scales[number]:
            bends.append((number, abs(turn(sub(stem_to, stem_from), sub(corner, handle)))))

    result = []
    for index in range(count):
        node = list(nodes[index])
        job = jobs.get(index)
        if job:
            s0, e0 = tuple(nodes[index - 1][:2]), tuple(node[:2])
            s1, e1 = moved((index - 1) % count), moved(index)
            start, end = job.get("start"), job.get("end")
            shared = bool(start and end and (start[1] is None or end[1] is None or start[1] + end[1] > 0.9))
            if shared:
                # Too short for two flares and a straight stretch: the sides meet at the middle.
                start, end = (start[0], 0.5), (end[0], 0.5)
            cursor0, cursor1 = s0, s1
            curved = False
            if start:
                number, split = start
                stop = e0 if split is None else lerp(s0, e0, split)
                stop1 = e1 if split is None else stop
                handles = quadratic(s1, lerp(s0, stop, 0.5), stop1)
                bend(number, stop, s0, handles[0], s1)
                result += [[*handles[0], "o"], [*handles[1], "o"]]
                if split is None:
                    curved = True
                else:
                    result.append([*stop, "c"])
                cursor0, cursor1 = stop, stop1
            if end:
                number, split = end
                if split is None or shared:
                    begin, begin1 = cursor0, cursor1
                else:
                    begin = begin1 = lerp(e0, s0, split)
                    result.append([*begin, "l"])
                handles = quadratic(begin1, lerp(begin, e0, 0.5), e1)
                bend(number, begin, e0, handles[1], e1)
                result += [[*handles[0], "o"], [*handles[1], "o"]]
                curved = True
            if curved:
                node[2] = "c" + node[2][1:]
        node[0], node[1] = moved(index)
        result.append(node)
    for node in result:
        node[0], node[1] = round(node[0]), round(node[1])
    return result, corners, bends


def closed_paths(layer):
    return [shape for shape in layer.get("shapes", []) if "nodes" in shape and shape.get("closed")]


def merged_outline(paths_nodes):
    """Short edges of the union of the paths, as (middle, length), and its contour count."""
    path = pathops.Path()
    pen = path.getPen()
    for nodes in paths_nodes:
        draw(nodes, pen)
    path.simplify(fix_winding=True)
    short = []
    contours = 0
    current = None
    for verb, points in path.segments:
        if verb == "moveTo":
            contours += 1
        elif points:
            edge = length(sub(points[-1], current))
            if NUB_MIN < edge < NUB_MAX:
                short.append((lerp(current, points[-1], 0.5), edge))
        if points:
            current = points[-1]
    return short, contours


def check_layer(base_paths, built, zones):
    """Return the (path, terminal) stroke ends of one flared layer that fail a check, with the reason.

    built holds build_path's result for each path of the flared layer; zones are the master's
    metric lines with their overshoots, as (low, high).
    """
    failures = {}
    if not base_paths:
        return failures
    all_corners = []
    for path_number, (_, corners, bends) in enumerate(built):
        sides = {}
        for number, angle in bends:
            sides.setdefault(number, []).append(angle)
            if angle > MAX_BEND:
                failures[(path_number, number)] = "bend"
        for number, angles in sides.items():
            if len(angles) == 2 and abs(angles[0] - angles[1]) > SYMMETRY_TOLERANCE:
                failures.setdefault((path_number, number), "symmetry")
        for number, old, new in corners:
            all_corners.append(((path_number, number), new))
            # A corner sitting on a metric line, or in its overshoot, must stay there.
            for low, high in zones:
                if low - 0.5 <= old[1] <= high + 0.5 and not low - 0.5 <= new[1] <= high + 0.5:
                    failures.setdefault((path_number, number), "metric line")
    base_short, base_contours = merged_outline(base_paths)
    flared_short, flared_contours = merged_outline([nodes for nodes, _, _ in built])
    if flared_contours != base_contours:
        for key, _ in all_corners:
            failures.setdefault(key, "contours")
    for middle, _ in flared_short:
        if any(length(sub(middle, other)) < NUB_MAX * 1.5 for other, _ in base_short):
            continue
        near = min(all_corners, key=lambda corner: length(sub(corner[1], middle)), default=None)
        if near and length(sub(near[1], middle)) <= NUB_REACH:
            failures.setdefault(near[0], "nub")
    return failures


def flare_glyph(glyph, masters, stems, metric_lines, zones):
    """Add the flared layers of a pilot glyph. Returns a report of its stroke ends."""
    layers = {layer["layerId"]: layer for layer in glyph["layers"]}
    reference = masters["reference"]
    reference_paths = closed_paths(layers[reference])
    polygons = [flatten(path["nodes"]) for path in reference_paths]
    plans = [
        plan_path(path["nodes"], stems[reference], metric_lines[reference], polygons[:i] + polygons[i + 1:])
        for i, path in enumerate(reference_paths)
    ]
    originals = {
        master_id: [path["nodes"] for path in closed_paths(layers[master_id])] for master_id in masters["all"]
    }
    zero = [[0.0] * len(terminals) for terminals, _ in plans]

    def build(master_id, layer_scales):
        return [
            build_path(nodes, terminals, jobs, layer_scales[i])
            for i, (nodes, (terminals, jobs)) in enumerate(zip(originals[master_id], plans))
        ]

    restructured = {
        master_id: [nodes for nodes, _, _ in build(master_id, zero)] for master_id in masters["all"]
    }
    # Roman and italic share the plan but are checked, and scaled back, separately.
    flared = {}
    reduced = {}
    for style, style_masters in masters["styles"].items():
        scales = [[1.0] * len(terminals) for terminals, _ in plans]
        reasons = {}
        while True:
            built = {master_id: build(master_id, scales) for master_id in style_masters}
            failures = {}
            for master_id in style_masters:
                failures.update(check_layer(restructured[master_id], built[master_id], zones[master_id]))
            if not failures:
                break
            for (path_number, number), reason in failures.items():
                scale = scales[path_number][number] * REDUCE_STEP
                scales[path_number][number] = scale if scale >= MIN_SCALE else 0.0
                reasons[(path_number, number)] = reason
        flared.update(built)
        for key, reason in reasons.items():
            reduced[(style, *key)] = (scales[key[0]][key[1]], reason)

    flared_layers = []
    for master_id in masters["all"]:
        layer = layers[master_id]
        flared_layer = copy.deepcopy(layer)
        flared_layer.pop("background", None)
        flared_layer["layerId"] = master_id + ID_SUFFIX
        for path, (nodes, _, _) in zip(closed_paths(flared_layer), flared[master_id]):
            path["nodes"] = nodes
        # The unflared layers take the same node structure, with their shape unchanged.
        for path, nodes in zip(closed_paths(layer), restructured[master_id]):
            path["nodes"] = nodes
        flared_layers.append(flared_layer)
    glyph["layers"].extend(flared_layers)
    return {"ends": sum(len(terminals) for terminals, _ in plans) * len(masters["styles"]), "reduced": reduced}


def reference_stem(glyph, master_id):
    """Width of the bottom of the reference glyph's stem in the given master."""
    layer = next(layer for layer in glyph["layers"] if layer["layerId"] == master_id)
    xs = [node[0] for node in layer["shapes"][0]["nodes"] if node[1] == 0]
    return max(xs) - min(xs)


def extend_location(holder, value):
    """Give a master or instance a position on the new axis."""
    if "axesValues" in holder:
        holder["axesValues"].append(value)
    for parameter in holder.get("customParameters", []):
        if parameter["name"] == "Axis Location":
            parameter["value"].append({"Axis": AXIS_NAME, "Location": value})


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--report", action="store_true", help="print the stroke ends flared in each glyph")
    args = parser.parse_args()

    font = load(PACKAGE / "fontinfo.plist")
    order = load(PACKAGE / "order.plist")
    glyphs = {}
    for path in (PACKAGE / "glyphs").glob("*.glyph"):
        glyph = load(path)
        glyphs[glyph["glyphname"]] = glyph

    base_masters = font["fontMaster"]
    roman = sorted(
        (master for master in base_masters if master["axesValues"][ITALIC_AXIS_INDEX] == 0),
        key=lambda master: master["axesValues"][WEIGHT_AXIS_INDEX],
    )
    masters = {
        "all": [master["id"] for master in base_masters],
        "styles": {
            "roman": [master["id"] for master in roman],
            "italic": [master["id"] for master in base_masters if master not in roman],
        },
        # Stroke ends are found in the middle weight and applied to every master.
        "reference": roman[len(roman) // 2]["id"],
    }
    stems = {master_id: reference_stem(glyphs[REFERENCE_STEM_GLYPH], master_id) for master_id in masters["styles"]["roman"]}
    metric_lines = {
        master["id"]: {metric.get("pos", 0) for metric in master["metricValues"]} for master in base_masters
    }

    zones = {
        master["id"]: [
            tuple(sorted((metric.get("pos", 0), metric.get("pos", 0) + metric.get("over", 0))))
            for metric in master["metricValues"]
        ]
        for master in base_masters
    }

    font["axes"].append({"name": AXIS_NAME, "tag": AXIS_TAG})
    flared_masters = []
    for master in base_masters:
        flared = copy.deepcopy(master)
        flared["id"] = master["id"] + ID_SUFFIX
        flared["name"] = master["name"] + NAME_SUFFIX
        extend_location(master, 0)
        extend_location(flared, AXIS_MAX)
        flared_masters.append(flared)
    font["fontMaster"] = base_masters + flared_masters
    for instance in font["instances"]:
        extend_location(instance, 0)
    for master_id in masters["all"]:
        if master_id in font.get("kerningLTR", {}):
            font["kerningLTR"][master_id + ID_SUFFIX] = copy.deepcopy(font["kerningLTR"][master_id])

    ends = reduced = dropped = 0
    for name in order:
        glyph = glyphs[name]
        if name in PILOT_GLYPHS and name not in SKIP_GLYPHS:
            report = flare_glyph(glyph, masters, stems, metric_lines, zones)
            ends += report["ends"]
            reduced += sum(1 for scale, _ in report["reduced"].values() if scale)
            dropped += sum(1 for scale, _ in report["reduced"].values() if not scale)
            if args.report:
                notes = ", ".join(
                    f"{key[0]} {reason} -> {scale:.0%}" if scale else f"{key[0]} {reason} -> dropped"
                    for key, (scale, reason) in report["reduced"].items()
                )
                print(f"{name:6} ends: {report['ends']}" + (f"  reduced: {notes}" if notes else ""))
            continue
        flared_layers = []
        for layer in glyph["layers"]:
            if layer["layerId"] not in masters["all"]:
                continue
            flared = copy.deepcopy(layer)
            flared.pop("background", None)
            flared["layerId"] = layer["layerId"] + ID_SUFFIX
            flared_layers.append(flared)
        glyph["layers"].extend(flared_layers)

    font["glyphs"] = [glyphs[name] for name in order]
    with open(OUTPUT, "w", encoding="utf-8") as fp:
        openstep_plist.dump(font, fp, indent=0, single_line_tuples=True)
    print(f"Wrote {OUTPUT.relative_to(SOURCES)}: {len(order)} glyphs, {len(font['fontMaster'])} masters")
    print(f"Stroke ends flared: {ends - dropped} ({reduced} scaled back, {dropped} dropped by the checks)")


if __name__ == "__main__":
    main()
