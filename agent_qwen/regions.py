"""Crop with context, then composite strictly inside the approved footprints."""

import math

from PIL import Image, ImageChops, ImageDraw, ImageFilter


def valid_polygon(points):
    if len(points) < 3:
        return False
    if any(not (0 <= p["x"] <= 1000 and 0 <= p["y"] <= 1000) for p in points):
        return False
    ring = list(zip(points, points[1:] + points[:1]))
    area = abs(sum(p["x"] * q["y"] - q["x"] * p["y"] for p, q in ring))
    return area > 0


def edit_polygons(intent):
    regions = intent["regions"]
    if not intent["regions_reliable"] or not regions or not all(valid_polygon(r["points"]) for r in regions):
        return []
    roles = {r["role"] for r in regions}
    required = {"origin", "destination"} if intent["operation"] == "move" else {"edit"}
    return [r["points"] for r in regions] if roles == required else []


def pixel_polygons(polygons, size):
    return [[(round(p["x"] * (size[0]-1) / 1000), round(p["y"] * (size[1]-1) / 1000))
             for p in polygon] for polygon in polygons]


def crop_input(source, polygons, output):
    with Image.open(source) as image:
        xy = [p for polygon in pixel_polygons(polygons, image.size) for p in polygon]
        xs, ys = zip(*xy)
        x0, x1, y0, y1 = min(xs), max(xs)+1, min(ys), max(ys)+1
        pad = max(24, math.ceil(max(x1-x0, y1-y0) * 0.30))
        box = [max(0, x0-pad), max(0, y0-pad), min(image.width, x1+pad), min(image.height, y1+pad)]
        image.crop(box).save(output)
        return box


def composite(base, generated, output, polygons, box=None):
    with Image.open(base) as original, Image.open(generated) as result:
        canvas = original.convert("RGB")
        box = box or [0, 0, *canvas.size]
        mask = Image.new("L", canvas.size)
        draw = ImageDraw.Draw(mask)
        for polygon in pixel_polygons(polygons, canvas.size):
            draw.polygon(polygon, fill=255)
        feather = max(1, min(canvas.size) / 250)
        # Clipping the blurred mask preserves every pixel outside the allowed regions,
        # including untouched space between the two ends of a move.
        mask = ImageChops.multiply(mask, mask.filter(ImageFilter.GaussianBlur(feather)))
        patch = result.convert("RGB").resize((box[2]-box[0], box[3]-box[1]), Image.Resampling.LANCZOS)
        canvas.paste(patch, (box[0], box[1]), mask.crop(box))
        canvas.save(output, format="PNG")
