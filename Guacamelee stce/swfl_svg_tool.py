import argparse
import json
import math
import re
import struct
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


SWFL_MAGIC = 0xFFAADF12
SUPPORTED_VERSION = 20


class SwflError(RuntimeError):
    pass


class Reader:
    def __init__(self, data: bytes, source: Path) -> None:
        self.data = data
        self.source = source
        self.offset = 0

    def tell(self) -> int:
        return self.offset

    def read(self, size: int) -> bytes:
        end = self.offset + size
        if end > len(self.data):
            raise SwflError(
                f"{self.source}: truncated at 0x{self.offset:x}; "
                f"wanted {size} bytes, has {len(self.data) - self.offset}"
            )
        chunk = self.data[self.offset : end]
        self.offset = end
        return chunk

    def u8(self) -> int:
        return self.read(1)[0]

    def u16(self) -> int:
        return struct.unpack("<H", self.read(2))[0]

    def u32(self) -> int:
        return struct.unpack("<I", self.read(4))[0]

    def f32(self) -> float:
        return struct.unpack("<f", self.read(4))[0]

    def f32_tuple(self, count: int) -> tuple[float, ...]:
        return struct.unpack("<" + "f" * count, self.read(count * 4))

    def ascii_string(self) -> str:
        length = self.u32()
        return self.read(length).decode("ascii", errors="replace")

    def utf16_string(self) -> str:
        length = self.u32()
        return self.read(length * 2).decode("utf-16le", errors="replace")


@dataclass(frozen=True)
class FillStyle:
    index: int
    kind: int
    rgba: tuple[int, int, int, int]


@dataclass(frozen=True)
class Segment:
    fill_index: int
    triangle_count: int
    vertex_start: int
    index_start: int
    unk0: int
    unk1: int


@dataclass(frozen=True)
class Shape:
    index: int
    offset: int
    segment_start: int
    segment_count: int
    object_id: int
    flags: tuple[int, int, int, int]
    bounds: tuple[float, float, float, float]
    texts: tuple[str, ...]


@dataclass(frozen=True)
class Offsets:
    segments_start: int
    segments_end: int
    vertices_start: int
    vertices_end: int
    indices_start: int
    indices_end: int


@dataclass(frozen=True)
class Mesh:
    source: Path
    data: bytes
    version: int
    fps: float
    stage: tuple[float, float, float, float]
    background: int
    textures: int
    fonts: list[tuple[str, int]]
    shapes: list[Shape]
    segments: list[Segment]
    vertices: list[tuple[float, float]]
    indices: list[int]
    fills: list[FillStyle]
    labels: list[str]
    offsets: Offsets


@dataclass(frozen=True)
class Triangle:
    segment: int
    fill_index: int
    points: tuple[tuple[float, float], tuple[float, float], tuple[float, float]]


def skip_data_blocks(reader: Reader) -> int:
    count = reader.u32()
    for _ in range(count):
        data_len = reader.u32()
        reader.read(4)
        extra_len = reader.u32()
        reader.read(data_len + extra_len)
    return count


def skip_nested_data_blocks(reader: Reader) -> int:
    count = reader.u32()
    for _ in range(count):
        reader.read(16)
        data_len = reader.u32()
        reader.read(4)
        extra_len = reader.u32()
        reader.read(data_len + extra_len)
    return count


def read_fonts(reader: Reader) -> list[tuple[str, int]]:
    fonts: list[tuple[str, int]] = []
    for _ in range(reader.u32()):
        fonts.append((reader.ascii_string(), reader.u16()))
    return fonts


def read_shapes(reader: Reader) -> list[Shape]:
    shapes: list[Shape] = []
    count = reader.u32()
    for index in range(count):
        offset = reader.tell()
        segment_start = reader.u32()
        _unknown_b = reader.u32()
        _unknown_c = reader.u32()
        object_id = reader.u16()
        segment_count = reader.u16()
        flags = (reader.u8(), reader.u8(), reader.u8(), reader.u8())
        if flags[1]:
            reader.read(2)
        bounds = reader.f32_tuple(4)
        texts: list[str] = []
        if flags[3]:
            text_count = reader.u32()
            for _ in range(text_count):
                texts.append(reader.utf16_string())
                reader.read(4 + 2 + 4 + 4 + 2 + 1)
        shapes.append(
            Shape(
                index=index,
                offset=offset,
                segment_start=segment_start,
                segment_count=segment_count,
                object_id=object_id,
                flags=flags,
                bounds=bounds,
                texts=tuple(texts),
            )
        )
    return shapes


def read_segments(reader: Reader) -> tuple[int, int, list[Segment]]:
    start = reader.tell()
    segments: list[Segment] = []
    for _ in range(reader.u32()):
        segments.append(
            Segment(
                fill_index=reader.u32(),
                triangle_count=reader.u16(),
                vertex_start=reader.u16(),
                index_start=reader.u16(),
                unk0=reader.u16(),
                unk1=reader.u16(),
            )
        )
    return start, reader.tell(), segments


def skip_small14_records(reader: Reader) -> None:
    count = reader.u32()
    reader.read(count * 14)


def read_vertices(reader: Reader) -> tuple[int, int, list[tuple[float, float]]]:
    start = reader.tell()
    vertices: list[tuple[float, float]] = []
    for buffer_index in range(reader.u32()):
        count = reader.u32()
        values = reader.f32_tuple(count * 2)
        parsed = [(values[i], values[i + 1]) for i in range(0, len(values), 2)]
        if buffer_index == 0:
            vertices = parsed
    return start, reader.tell(), vertices


def read_indices(reader: Reader) -> tuple[int, int, list[int]]:
    start = reader.tell()
    indices: list[int] = []
    for buffer_index in range(reader.u32()):
        count = reader.u32()
        values = list(struct.unpack("<" + "H" * count, reader.read(count * 2)))
        if buffer_index == 0:
            indices = values
    return start, reader.tell(), indices


def float_to_byte(value: float) -> int:
    if not math.isfinite(value):
        return 255
    return max(0, min(255, int(round(value * 255.0))))


def read_fills(reader: Reader) -> list[FillStyle]:
    fills: list[FillStyle] = []
    for index in range(reader.u32()):
        reader.read(64)
        rgba_f = reader.f32_tuple(4)
        kind = reader.u8()
        reader.read(2)
        if kind in (0x10, 0x12) or 0x40 <= kind <= 0x43:
            reader.read(2)
        reader.read(3)
        fills.append(FillStyle(index=index, kind=kind, rgba=tuple(float_to_byte(v) for v in rgba_f)))  # type: ignore[arg-type]
    return fills


def skip_rect_records(reader: Reader) -> None:
    count = reader.u32()
    reader.read(count * 40)


def skip_small12(reader: Reader) -> None:
    count = reader.u32()
    reader.read(count * 12)


def skip_u32_pairs(reader: Reader) -> None:
    count = reader.u32()
    reader.read(count * 8)


def skip_small10(reader: Reader) -> None:
    count = reader.u32()
    reader.read(count * 10)


def read_label_bytes(reader: Reader) -> list[str]:
    count = reader.u32()
    raw = reader.read(count)
    return [p.decode("ascii", errors="ignore") for p in raw.split(b"\0") if len(p) >= 3]


def skip_matrices(reader: Reader) -> None:
    count = reader.u32()
    reader.read(count * 24)


def skip_float8(reader: Reader) -> None:
    count = reader.u32()
    reader.read(count * 32)


def skip_small7(reader: Reader) -> None:
    count = reader.u32()
    reader.read(count * 7)


def skip_map(reader: Reader) -> None:
    count = reader.u32()
    reader.read(count * 8)


def skip_shorts(reader: Reader) -> None:
    count = reader.u32()
    reader.read(count * 2)


def parse_mesh(path: Path) -> Mesh:
    data = path.read_bytes()
    reader = Reader(data, path)
    magic = reader.u32()
    if magic != SWFL_MAGIC:
        raise SwflError(f"{path}: unsupported SWFL magic 0x{magic:08x}")
    version = reader.u32()
    if version != SUPPORTED_VERSION:
        raise SwflError(f"{path}: unsupported SWFL version {version}")
    reader.ascii_string()
    fps = reader.f32()
    stage = reader.f32_tuple(4)
    background = reader.u32()

    skip_data_blocks(reader)
    textures = skip_data_blocks(reader)
    skip_nested_data_blocks(reader)
    fonts = read_fonts(reader)
    shapes = read_shapes(reader)
    segments_start, segments_end, segments = read_segments(reader)
    skip_small14_records(reader)
    vertices_start, vertices_end, vertices = read_vertices(reader)
    indices_start, indices_end, indices = read_indices(reader)
    fills = read_fills(reader)

    skip_rect_records(reader)
    skip_small12(reader)
    skip_u32_pairs(reader)
    skip_small12(reader)
    skip_small10(reader)
    labels = read_label_bytes(reader)
    skip_matrices(reader)
    skip_float8(reader)
    skip_small7(reader)
    skip_map(reader)
    skip_shorts(reader)
    reader.read(8)
    skip_map(reader)
    if reader.tell() != len(data):
        raise SwflError(
            f"{path}: parser stopped at 0x{reader.tell():x}, len=0x{len(data):x}"
        )

    return Mesh(
        source=path,
        data=data,
        version=version,
        fps=fps,
        stage=stage,
        background=background,
        textures=textures,
        fonts=fonts,
        shapes=shapes,
        segments=segments,
        vertices=vertices,
        indices=indices,
        fills=fills,
        labels=labels,
        offsets=Offsets(
            segments_start,
            segments_end,
            vertices_start,
            vertices_end,
            indices_start,
            indices_end,
        ),
    )


def color_for_fill(mesh: Mesh, fill_index: int) -> tuple[int, int, int, int]:
    if 0 <= fill_index < len(mesh.fills):
        return mesh.fills[fill_index].rgba
    return (255, 255, 255, 255)


def triangles_for_segments(mesh: Mesh, segment_ids: Iterable[int]) -> list[Triangle]:
    out: list[Triangle] = []
    for segment_id in segment_ids:
        if segment_id < 0 or segment_id >= len(mesh.segments):
            continue
        segment = mesh.segments[segment_id]
        start = segment.index_start
        end = start + segment.triangle_count * 3
        if end > len(mesh.indices):
            continue
        for offset in range(start, end, 3):
            points: list[tuple[float, float]] = []
            for relative_index in mesh.indices[offset : offset + 3]:
                vertex_index = segment.vertex_start + relative_index
                if vertex_index >= len(mesh.vertices):
                    points = []
                    break
                points.append(mesh.vertices[vertex_index])
            if len(points) == 3:
                out.append(
                    Triangle(
                        segment_id,
                        segment.fill_index,
                        (points[0], points[1], points[2]),
                    )
                )
    return out


def triangle_bounds(triangles: list[Triangle]) -> tuple[float, float, float, float]:
    xs = [x for triangle in triangles for x, _ in triangle.points]
    ys = [y for triangle in triangles for _, y in triangle.points]
    if not xs or not ys:
        return (0.0, 0.0, 1.0, 1.0)
    return (min(xs), min(ys), max(xs), max(ys))


def triangle_svg_viewbox(
    triangles: list[Triangle],
) -> tuple[float, float, float, float]:
    min_x, min_y, max_x, max_y = triangle_bounds(triangles)
    return (min_x, -max_y, max(max_x - min_x, 1.0), max(max_y - min_y, 1.0))


def parse_viewbox(text: str | None) -> tuple[float, float, float, float] | None:
    values = parse_numbers(text or "")
    if len(values) < 4 or values[2] == 0.0 or values[3] == 0.0:
        return None
    return (values[0], values[1], values[2], values[3])


def transform_triangles_svg_viewbox(
    triangles: list[Triangle],
    source_viewbox: tuple[float, float, float, float],
    target_viewbox: tuple[float, float, float, float],
    preserve_aspect: bool = True,
) -> list[Triangle]:
    src_x, src_y, src_w, src_h = source_viewbox
    dst_x, dst_y, dst_w, dst_h = target_viewbox
    if src_w == 0.0 or src_h == 0.0:
        return triangles
    if preserve_aspect:
        scale = min(dst_w / src_w, dst_h / src_h)
        mapped_w = src_w * scale
        mapped_h = src_h * scale
        offset_x = dst_x + (dst_w - mapped_w) * 0.5
        offset_y = dst_y + (dst_h - mapped_h) * 0.5
        scale_x = scale_y = scale
    else:
        offset_x = dst_x
        offset_y = dst_y
        scale_x = dst_w / src_w
        scale_y = dst_h / src_h

    def transform_point(point: tuple[float, float]) -> tuple[float, float]:
        svg_x, svg_y = point[0], -point[1]
        mapped_x = offset_x + (svg_x - src_x) * scale_x
        mapped_y = offset_y + (svg_y - src_y) * scale_y
        return (mapped_x, -mapped_y)

    return [
        Triangle(
            triangle.segment,
            triangle.fill_index,
            (
                transform_point(triangle.points[0]),
                transform_point(triangle.points[1]),
                transform_point(triangle.points[2]),
            ),
        )
        for triangle in triangles
    ]


def transform_triangles_bounds(
    triangles: list[Triangle],
    source_bounds: tuple[float, float, float, float],
    target_bounds: tuple[float, float, float, float],
) -> list[Triangle]:
    src_min_x, src_min_y, src_max_x, src_max_y = source_bounds
    dst_min_x, dst_min_y, dst_max_x, dst_max_y = target_bounds
    src_w = src_max_x - src_min_x
    src_h = src_max_y - src_min_y
    if src_w == 0.0 or src_h == 0.0:
        return triangles
    scale_x = (dst_max_x - dst_min_x) / src_w
    scale_y = (dst_max_y - dst_min_y) / src_h

    def transform_point(point: tuple[float, float]) -> tuple[float, float]:
        return (
            dst_min_x + (point[0] - src_min_x) * scale_x,
            dst_min_y + (point[1] - src_min_y) * scale_y,
        )

    return [
        Triangle(
            triangle.segment,
            triangle.fill_index,
            (
                transform_point(triangle.points[0]),
                transform_point(triangle.points[1]),
                transform_point(triangle.points[2]),
            ),
        )
        for triangle in triangles
    ]


def fmt(value: float) -> str:
    if abs(value) < 0.000001:
        value = 0.0
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return text if text else "0"


def svg_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def write_svg(
    path: Path,
    mesh: Mesh,
    triangles: list[Triangle],
    kind: str,
    name: str,
    extra: dict[str, Any] | None = None,
) -> None:
    min_x, min_y, max_x, max_y = triangle_bounds(triangles)
    width = max(max_x - min_x, 1.0)
    height = max(max_y - min_y, 1.0)
    view_min_y = -max_y
    attrs = {
        "data-swfl-kind": kind,
        "data-swfl-name": name,
        "data-swfl-source": str(mesh.source),
        "data-swfl-y-flip": "1",
    }
    if extra:
        attrs.update({f"data-swfl-{k}": str(v) for k, v in extra.items()})
    attr_text = " ".join(f'{key}="{svg_escape(value)}"' for key, value in attrs.items())

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" version="1.1" '
            f'viewBox="{fmt(min_x)} {fmt(view_min_y)} {fmt(width)} {fmt(height)}" {attr_text}>'
        ),
        f"  <title>{svg_escape(name)}</title>",
        '  <g id="swfl-mesh" fill-rule="nonzero" stroke="none">',
    ]

    by_segment: dict[int, list[Triangle]] = {}
    for triangle in triangles:
        by_segment.setdefault(triangle.segment, []).append(triangle)

    tri_index = 0
    for segment_id in sorted(by_segment):
        segment = (
            mesh.segments[segment_id] if 0 <= segment_id < len(mesh.segments) else None
        )
        fill_index = (
            segment.fill_index
            if segment is not None
            else by_segment[segment_id][0].fill_index
        )
        color = color_for_fill(mesh, fill_index)
        fill = f"#{color[0]:02x}{color[1]:02x}{color[2]:02x}"
        opacity = "" if color[3] >= 255 else f' fill-opacity="{color[3] / 255.0:.6f}"'
        lines.append(
            f'    <g id="segment_{segment_id:04d}" data-segment="{segment_id}" '
            f'data-fill-index="{fill_index}" fill="{fill}"{opacity}>'
        )
        for triangle in by_segment[segment_id]:
            points = " ".join(f"{fmt(x)},{fmt(-y)}" for x, y in triangle.points)
            lines.append(
                f'      <polygon data-triangle="{tri_index}" points="{points}" />'
            )
            tri_index += 1
        lines.append("    </g>")
    lines.extend(["  </g>", "</svg>", ""])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def shape_segment_ids(mesh: Mesh, shape: Shape) -> list[int]:
    if shape.segment_count <= 0:
        return []
    end = shape.segment_start + shape.segment_count
    if shape.segment_start < 0 or end > len(mesh.segments):
        return []
    return list(range(shape.segment_start, end))


def export_one(input_file: Path, out_dir: Path) -> dict[str, Any]:
    mesh = parse_mesh(input_file.resolve())
    all_segment_ids = list(range(len(mesh.segments)))
    full_triangles = triangles_for_segments(mesh, all_segment_ids)
    write_svg(out_dir / "full.svg", mesh, full_triangles, "full", mesh.source.stem)

    covered: set[int] = set()
    shape_reports: list[dict[str, Any]] = []
    shapes_dir = out_dir / "shapes"
    for shape in mesh.shapes:
        segment_ids = shape_segment_ids(mesh, shape)
        if not segment_ids:
            continue
        covered.update(segment_ids)
        triangles = triangles_for_segments(mesh, segment_ids)
        filename = f"shape_{shape.index:04d}_obj_{shape.object_id:04d}_seg_{shape.segment_start:04d}_{shape.segment_count:04d}.svg"
        write_svg(
            shapes_dir / filename,
            mesh,
            triangles,
            "shape",
            f"shape {shape.index}",
            {
                "shape-index": shape.index,
                "object-id": shape.object_id,
                "segment-start": shape.segment_start,
                "segment-count": shape.segment_count,
            },
        )
        shape_reports.append(
            {
                "shape": shape.index,
                "object_id": shape.object_id,
                "segment_start": shape.segment_start,
                "segment_count": shape.segment_count,
                "triangles": len(triangles),
                "bounds": shape.bounds,
            }
        )

    unassigned = [
        segment_id for segment_id in all_segment_ids if segment_id not in covered
    ]
    if unassigned:
        write_svg(
            out_dir / "unassigned_segments.svg",
            mesh,
            triangles_for_segments(mesh, unassigned),
            "unassigned",
            "unassigned segments",
            {"segments": ",".join(str(segment_id) for segment_id in unassigned)},
        )
        split_dir = out_dir / "unassigned_segments"
        for segment_id in unassigned:
            write_svg(
                split_dir / f"segment_{segment_id:04d}.svg",
                mesh,
                triangles_for_segments(mesh, [segment_id]),
                "segment",
                f"segment {segment_id}",
                {"segment": segment_id},
            )

    text_refs = [
        {"shape": shape.index, "object_id": shape.object_id, "texts": shape.texts}
        for shape in mesh.shapes
        if shape.texts
    ]
    manifest = {
        "source": str(mesh.source),
        "version": mesh.version,
        "fps": mesh.fps,
        "stage": mesh.stage,
        "textures": mesh.textures,
        "fonts": mesh.fonts,
        "labels": mesh.labels,
        "segments": len(mesh.segments),
        "vertices": len(mesh.vertices),
        "indices": len(mesh.indices),
        "fills": len(mesh.fills),
        "triangles": len(full_triangles),
        "shapes": shape_reports,
        "unassigned_segments": unassigned,
        "text_refs": text_refs,
        "import_note": "Import accepts exported polygons and triangulates SVG paths/text/rects/circles automatically.",
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return manifest


def command_export(args: argparse.Namespace) -> int:
    input_path = args.input.resolve()
    out_dir = args.output.resolve()
    if input_path.is_file():
        manifest = export_one(input_path, out_dir)
        print(f"Exported SVG mesh set: {out_dir}")
        print(f"  full.svg: {manifest['triangles']} triangles")
        print(f"  shapes: {len(manifest['shapes'])}")
        print(f"  unassigned segments: {len(manifest['unassigned_segments'])}")
        return 0

    if not input_path.is_dir():
        raise SwflError(f"input does not exist: {input_path}")

    reports: list[dict[str, Any]] = []
    for swfl_path in sorted(input_path.rglob("*.swfl")):
        rel = swfl_path.relative_to(input_path)
        file_out_dir = out_dir / rel.with_suffix("")
        manifest = export_one(swfl_path, file_out_dir)
        reports.append(
            {
                "source": str(swfl_path),
                "output": str(file_out_dir),
                "triangles": manifest["triangles"],
                "shapes": len(manifest["shapes"]),
                "unassigned_segments": len(manifest["unassigned_segments"]),
            }
        )
        print(f"OK {swfl_path} -> {file_out_dir}")

    if not reports:
        raise SwflError(f"no .swfl files found under {input_path}")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "batch_manifest.json").write_text(
        json.dumps(reports, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"Exported {len(reports)} SWFL files to {out_dir}")
    return 0


Transform = tuple[float, float, float, float, float, float]


def mat_mul(left: Transform, right: Transform) -> Transform:
    a, b, c, d, e, f = left
    g, h, i, j, k, l = right
    return (
        a * g + c * h,
        b * g + d * h,
        a * i + c * j,
        b * i + d * j,
        a * k + c * l + e,
        b * k + d * l + f,
    )


def apply_mat(matrix: Transform, x: float, y: float) -> tuple[float, float]:
    a, b, c, d, e, f = matrix
    return (a * x + c * y + e, b * x + d * y + f)


TRANSFORM_RE = re.compile(r"([a-zA-Z]+)\(([^)]*)\)")
NUMBER_RE = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?")


def parse_numbers(text: str) -> list[float]:
    return [float(match.group(0)) for match in NUMBER_RE.finditer(text)]


def parse_transform(text: str | None) -> Transform:
    matrix: Transform = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    if not text:
        return matrix
    for name, values_text in TRANSFORM_RE.findall(text):
        values = parse_numbers(values_text)
        name = name.lower()
        current: Transform
        if name == "matrix" and len(values) >= 6:
            current = (values[0], values[1], values[2], values[3], values[4], values[5])
        elif name == "translate":
            tx = values[0] if values else 0.0
            ty = values[1] if len(values) > 1 else 0.0
            current = (1.0, 0.0, 0.0, 1.0, tx, ty)
        elif name == "scale":
            sx = values[0] if values else 1.0
            sy = values[1] if len(values) > 1 else sx
            current = (sx, 0.0, 0.0, sy, 0.0, 0.0)
        elif name == "rotate" and values:
            angle = math.radians(values[0])
            cos_a = math.cos(angle)
            sin_a = math.sin(angle)
            rot = (cos_a, sin_a, -sin_a, cos_a, 0.0, 0.0)
            if len(values) >= 3:
                cx, cy = values[1], values[2]
                current = mat_mul(
                    mat_mul((1.0, 0.0, 0.0, 1.0, cx, cy), rot),
                    (1.0, 0.0, 0.0, 1.0, -cx, -cy),
                )
            else:
                current = rot
        else:
            continue
        matrix = mat_mul(matrix, current)
    return matrix


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_points(text: str) -> list[tuple[float, float]]:
    values = parse_numbers(text)
    if len(values) < 6 or len(values) % 2:
        return []
    return [(values[i], values[i + 1]) for i in range(0, len(values), 2)]


def parse_int_list(text: str | None) -> list[int]:
    if not text:
        return []
    out: list[int] = []
    for part in re.split(r"[,;\s]+", text.strip()):
        if not part:
            continue
        try:
            out.append(int(part))
        except ValueError:
            pass
    return out


def parse_style(style: str | None) -> dict[str, str]:
    result: dict[str, str] = {}
    if not style:
        return result
    for item in style.split(";"):
        if ":" not in item:
            continue
        key, value = item.split(":", 1)
        result[key.strip().lower()] = value.strip()
    return result


CSS_CLASS_RE = re.compile(r"\.([A-Za-z_][\w-]*)\s*\{([^{}]*)\}", re.DOTALL)


def collect_css_classes(root: ET.Element) -> dict[str, dict[str, str]]:
    classes: dict[str, dict[str, str]] = {}
    for node in root.iter():
        if local_name(node.tag).lower() != "style":
            continue
        css = "".join(node.itertext())
        for class_name, body in CSS_CLASS_RE.findall(css):
            classes[class_name] = parse_style(body)
    return classes


def node_style(
    node: ET.Element, css_classes: dict[str, dict[str, str]] | None = None
) -> dict[str, str]:
    style: dict[str, str] = {}
    if css_classes:
        for class_name in node.attrib.get("class", "").split():
            style.update(css_classes.get(class_name, {}))
    style.update(parse_style(node.attrib.get("style")))
    return style


def attr_value(
    node: ET.Element,
    name: str,
    default: str | None = None,
    css_classes: dict[str, dict[str, str]] | None = None,
) -> str | None:
    if name in node.attrib:
        return node.attrib[name]
    return node_style(node, css_classes).get(name, default)


def attr_float(node: ET.Element, name: str, default: float = 0.0) -> float:
    value = attr_value(node, name)
    if value is None:
        return default
    numbers = parse_numbers(value)
    return numbers[0] if numbers else default


def node_segment(node: ET.Element, inherited: int | None) -> int | None:
    for key in ("data-segment", "data-swfl-segment"):
        value = node.attrib.get(key)
        if value is not None:
            try:
                return int(value)
            except ValueError:
                pass
    node_id = node.attrib.get("id", "")
    match = re.search(r"(?:^|[_-])segment[_-]?(\d+)(?:$|[_-])", node_id)
    if match:
        return int(match.group(1))
    return inherited


def node_fill_index(node: ET.Element, inherited: int) -> int:
    for key in ("data-fill-index", "data-swfl-fill-index"):
        value = node.attrib.get(key)
        if value is not None:
            try:
                return int(value)
            except ValueError:
                pass
    return inherited


def is_drawn(
    node: ET.Element, css_classes: dict[str, dict[str, str]] | None = None
) -> bool:
    style = node_style(node, css_classes)
    if node.attrib.get("display") == "none" or style.get("display") == "none":
        return False
    if node.attrib.get("visibility") == "hidden" or style.get("visibility") == "hidden":
        return False
    fill = node.attrib.get("fill", style.get("fill"))
    return fill != "none"


def cubic_point(
    p0: tuple[float, float],
    p1: tuple[float, float],
    p2: tuple[float, float],
    p3: tuple[float, float],
    t: float,
) -> tuple[float, float]:
    mt = 1.0 - t
    return (
        mt * mt * mt * p0[0]
        + 3 * mt * mt * t * p1[0]
        + 3 * mt * t * t * p2[0]
        + t * t * t * p3[0],
        mt * mt * mt * p0[1]
        + 3 * mt * mt * t * p1[1]
        + 3 * mt * t * t * p2[1]
        + t * t * t * p3[1],
    )


def quad_point(
    p0: tuple[float, float],
    p1: tuple[float, float],
    p2: tuple[float, float],
    t: float,
) -> tuple[float, float]:
    mt = 1.0 - t
    return (
        mt * mt * p0[0] + 2 * mt * t * p1[0] + t * t * p2[0],
        mt * mt * p0[1] + 2 * mt * t * p1[1] + t * t * p2[1],
    )


PATH_TOKEN_RE = re.compile(
    r"[AaCcHhLlMmQqSsTtVvZz]|[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
)


def path_contours(d: str, curve_steps: int = 16) -> list[list[tuple[float, float]]]:
    tokens = PATH_TOKEN_RE.findall(d)
    contours: list[list[tuple[float, float]]] = []
    current: list[tuple[float, float]] = []
    i = 0
    command = ""
    x = y = 0.0
    start = (0.0, 0.0)
    last_cubic: tuple[float, float] | None = None
    last_quad: tuple[float, float] | None = None

    def is_cmd(token: str) -> bool:
        return len(token) == 1 and token.isalpha()

    def number() -> float:
        nonlocal i
        if i >= len(tokens) or is_cmd(tokens[i]):
            raise SwflError("malformed SVG path data")
        value = float(tokens[i])
        i += 1
        return value

    def has_number() -> bool:
        return i < len(tokens) and not is_cmd(tokens[i])

    def add_point(px: float, py: float) -> None:
        nonlocal x, y
        x, y = px, py
        if not current or current[-1] != (px, py):
            current.append((px, py))

    def finish(close: bool = False) -> None:
        nonlocal current
        if close and current and current[0] != current[-1]:
            current.append(current[0])
        if len(current) >= 3:
            contours.append(current)
        current = []

    while i < len(tokens):
        if is_cmd(tokens[i]):
            command = tokens[i]
            i += 1
        if not command:
            raise SwflError("SVG path data starts without a command")

        relative = command.islower()
        cmd = command.upper()

        if cmd == "M":
            px, py = number(), number()
            if relative:
                px += x
                py += y
            finish()
            current = [(px, py)]
            x, y = px, py
            start = (x, y)
            last_cubic = None
            last_quad = None
            command = "l" if relative else "L"
            while has_number():
                px, py = number(), number()
                if relative:
                    px += x
                    py += y
                add_point(px, py)
        elif cmd == "L":
            while has_number():
                px, py = number(), number()
                if relative:
                    px += x
                    py += y
                add_point(px, py)
            last_cubic = None
            last_quad = None
        elif cmd == "H":
            while has_number():
                px = number()
                if relative:
                    px += x
                add_point(px, y)
            last_cubic = None
            last_quad = None
        elif cmd == "V":
            while has_number():
                py = number()
                if relative:
                    py += y
                add_point(x, py)
            last_cubic = None
            last_quad = None
        elif cmd == "C":
            while has_number():
                p0 = (x, y)
                p1 = (number(), number())
                p2 = (number(), number())
                p3 = (number(), number())
                if relative:
                    p1 = (p1[0] + x, p1[1] + y)
                    p2 = (p2[0] + x, p2[1] + y)
                    p3 = (p3[0] + x, p3[1] + y)
                for step in range(1, curve_steps + 1):
                    add_point(*cubic_point(p0, p1, p2, p3, step / curve_steps))
                last_cubic = p2
                last_quad = None
        elif cmd == "S":
            while has_number():
                p0 = (x, y)
                p1 = (
                    (2 * x - last_cubic[0], 2 * y - last_cubic[1]) if last_cubic else p0
                )
                p2 = (number(), number())
                p3 = (number(), number())
                if relative:
                    p2 = (p2[0] + x, p2[1] + y)
                    p3 = (p3[0] + x, p3[1] + y)
                for step in range(1, curve_steps + 1):
                    add_point(*cubic_point(p0, p1, p2, p3, step / curve_steps))
                last_cubic = p2
                last_quad = None
        elif cmd == "Q":
            while has_number():
                p0 = (x, y)
                p1 = (number(), number())
                p2 = (number(), number())
                if relative:
                    p1 = (p1[0] + x, p1[1] + y)
                    p2 = (p2[0] + x, p2[1] + y)
                for step in range(1, curve_steps + 1):
                    add_point(*quad_point(p0, p1, p2, step / curve_steps))
                last_quad = p1
                last_cubic = None
        elif cmd == "T":
            while has_number():
                p0 = (x, y)
                p1 = (2 * x - last_quad[0], 2 * y - last_quad[1]) if last_quad else p0
                p2 = (number(), number())
                if relative:
                    p2 = (p2[0] + x, p2[1] + y)
                for step in range(1, curve_steps + 1):
                    add_point(*quad_point(p0, p1, p2, step / curve_steps))
                last_quad = p1
                last_cubic = None
        elif cmd == "A":
            while has_number():
                # The SWFL text/editing workflow mainly emits cubic paths.
                # For unsupported SVG arcs, keep the endpoint so import still works.
                number()
                number()
                number()
                number()
                number()
                px, py = number(), number()
                if relative:
                    px += x
                    py += y
                add_point(px, py)
            last_cubic = None
            last_quad = None
        elif cmd == "Z":
            add_point(*start)
            finish(close=True)
            x, y = start
            last_cubic = None
            last_quad = None
        else:
            raise SwflError(f"unsupported SVG path command: {command}")

    finish()
    return contours


def rect_contours(node: ET.Element) -> list[list[tuple[float, float]]]:
    x = attr_float(node, "x")
    y = attr_float(node, "y")
    width = attr_float(node, "width")
    height = attr_float(node, "height")
    if width <= 0 or height <= 0:
        return []
    return [[(x, y), (x + width, y), (x + width, y + height), (x, y + height), (x, y)]]


def ellipse_contours(
    node: ET.Element, circle: bool = False, steps: int = 40
) -> list[list[tuple[float, float]]]:
    cx = attr_float(node, "cx")
    cy = attr_float(node, "cy")
    if circle:
        rx = ry = attr_float(node, "r")
    else:
        rx = attr_float(node, "rx")
        ry = attr_float(node, "ry")
    if rx <= 0 or ry <= 0:
        return []
    points = [
        (
            cx + math.cos((math.pi * 2.0 * step) / steps) * rx,
            cy + math.sin((math.pi * 2.0 * step) / steps) * ry,
        )
        for step in range(steps)
    ]
    points.append(points[0])
    return [points]


def text_contours(node: ET.Element) -> list[list[tuple[float, float]]]:
    text = "".join(node.itertext())
    if not text.strip():
        return []
    try:
        from matplotlib.font_manager import FontProperties
        from matplotlib.path import Path as MplPath
        from matplotlib.textpath import TextPath
    except Exception as exc:
        raise SwflError("SVG text import requires matplotlib") from exc

    x = attr_float(node, "x")
    y = attr_float(node, "y")
    size = attr_float(node, "font-size", 16.0)
    family = attr_value(node, "font-family")
    prop = FontProperties(family=family.strip("'\"") if family else None)
    text_path = TextPath((0, 0), text, size=size, prop=prop)

    contours: list[list[tuple[float, float]]] = []
    current: list[tuple[float, float]] = []
    for verts, code in text_path.iter_segments(curves=False, simplify=False):
        if len(verts) < 2:
            continue
        point = (x + float(verts[0]), y - float(verts[1]))
        if code == MplPath.MOVETO:
            if len(current) >= 3:
                contours.append(current)
            current = [point]
        elif code == MplPath.LINETO:
            if not current:
                current = [point]
            elif current[-1] != point:
                current.append(point)
        elif code == MplPath.CLOSEPOLY:
            if current and current[0] != current[-1]:
                current.append(current[0])
            if len(current) >= 3:
                contours.append(current)
            current = []
    if len(current) >= 3:
        contours.append(current)
    return contours


def points_close(
    a: tuple[float, float], b: tuple[float, float], eps: float = 1e-5
) -> bool:
    return abs(a[0] - b[0]) <= eps and abs(a[1] - b[1]) <= eps


def point_on_segment(
    px: float, py: float, ax: float, ay: float, bx: float, by: float
) -> bool:
    length_sq = (bx - ax) ** 2 + (by - ay) ** 2
    if length_sq <= 1e-10:
        return (px - ax) ** 2 + (py - ay) ** 2 <= 1e-10
    if px < min(ax, bx) - 1e-5 or px > max(ax, bx) + 1e-5:
        return False
    if py < min(ay, by) - 1e-5 or py > max(ay, by) + 1e-5:
        return False
    cross = (px - ax) * (by - ay) - (py - ay) * (bx - ax)
    if abs(cross) > 1e-5:
        return False
    dot = (px - ax) * (bx - ax) + (py - ay) * (by - ay)
    if dot < -1e-5:
        return False
    return dot <= length_sq + 1e-5


def point_in_contour(x: float, y: float, contour: list[tuple[float, float]]) -> bool:
    inside = False
    if len(contour) < 3:
        return False
    xs = [px for px, _ in contour]
    ys = [py for _, py in contour]
    if (
        x < min(xs) - 1e-5
        or x > max(xs) + 1e-5
        or y < min(ys) - 1e-5
        or y > max(ys) + 1e-5
    ):
        return False
    prev_x, prev_y = contour[-1]
    for curr_x, curr_y in contour:
        if point_on_segment(x, y, prev_x, prev_y, curr_x, curr_y):
            return True
        intersects = (curr_y > y) != (prev_y > y)
        if intersects:
            x_at_y = (prev_x - curr_x) * (y - curr_y) / (
                prev_y - curr_y + 1e-30
            ) + curr_x
            if x < x_at_y:
                inside = not inside
        prev_x, prev_y = curr_x, curr_y
    return inside


def point_in_compound(
    x: float, y: float, contours: list[list[tuple[float, float]]]
) -> bool:
    return sum(1 for contour in contours if point_in_contour(x, y, contour)) % 2 == 1


def clean_contours(
    contours: list[list[tuple[float, float]]],
) -> list[list[tuple[float, float]]]:
    cleaned: list[list[tuple[float, float]]] = []
    for contour in contours:
        out: list[tuple[float, float]] = []
        for x, y in contour:
            if not (math.isfinite(x) and math.isfinite(y)):
                continue
            point = (float(x), float(y))
            if not out or not points_close(out[-1], point):
                out.append(point)
        if len(out) >= 3:
            cleaned.append(out)
    return cleaned


def open_contour(contour: list[tuple[float, float]]) -> list[tuple[float, float]]:
    if len(contour) > 3 and points_close(contour[0], contour[-1]):
        return contour[:-1]
    return contour


def contour_area(contour: list[tuple[float, float]]) -> float:
    points = open_contour(contour)
    area = 0.0
    for i, (x1, y1) in enumerate(points):
        x2, y2 = points[(i + 1) % len(points)]
        area += x1 * y2 - x2 * y1
    return area * 0.5


def contour_parents(contours: list[list[tuple[float, float]]]) -> list[int]:
    areas = [abs(contour_area(contour)) for contour in contours]
    parents: list[int] = [-1] * len(contours)
    for child_index, contour in enumerate(contours):
        if not contour:
            continue
        px, py = open_contour(contour)[0]
        candidates = [
            parent_index
            for parent_index, parent in enumerate(contours)
            if parent_index != child_index
            and areas[parent_index] > areas[child_index] + 1e-5
            and point_in_contour(px, py, parent)
        ]
        if candidates:
            parents[child_index] = min(candidates, key=lambda index: areas[index])
    return parents


def contour_depths(parents: list[int]) -> list[int]:
    depths: list[int | None] = [None] * len(parents)

    def depth(index: int) -> int:
        if depths[index] is not None:
            return depths[index]
        parent = parents[index]
        if parent < 0:
            depths[index] = 0
        else:
            depths[index] = depth(parent) + 1
        return depths[index]

    return [depth(index) for index in range(len(parents))]


def fan_triangles(
    points: list[tuple[float, float]], segment_id: int, fill_index: int
) -> list[Triangle]:
    if len(points) > 3 and points[0] == points[-1]:
        points = points[:-1]
    if len(points) < 3:
        return []
    first = points[0]
    triangles: list[Triangle] = []
    for i in range(1, len(points) - 1):
        native = (
            (first[0], -first[1]),
            (points[i][0], -points[i][1]),
            (points[i + 1][0], -points[i + 1][1]),
        )
        triangles.append(Triangle(segment_id, fill_index, native))
    return triangles


def triangulate_contours_earcut(
    contours: list[list[tuple[float, float]]],
    segment_id: int,
    fill_index: int,
    fill_rule: str = "nonzero",
) -> list[Triangle] | None:
    try:
        import numpy as np
        import mapbox_earcut as earcut
    except Exception:
        return None

    rings = [open_contour(contour) for contour in contours]
    rings = [
        ring for ring in rings if len(ring) >= 3 and abs(contour_area(ring)) > 1e-5
    ]
    if not rings:
        return []

    parents = contour_parents(rings)
    depths = contour_depths(parents)
    signs = [1 if contour_area(ring) >= 0.0 else -1 for ring in rings]
    if fill_rule.lower() == "evenodd":
        filled = [depth % 2 == 0 for depth in depths]
    else:
        filled: list[bool | None] = [None] * len(rings)

        def is_filled(index: int) -> bool:
            if filled[index] is not None:
                return filled[index]
            parent = parents[index]
            if parent < 0:
                filled[index] = True
            elif signs[index] == signs[parent]:
                filled[index] = is_filled(parent)
            else:
                filled[index] = not is_filled(parent)
            return filled[index]

        filled = [is_filled(index) for index in range(len(rings))]

    def nearest_filled_ancestor(index: int) -> int:
        parent = parents[index]
        while parent >= 0:
            if filled[parent]:
                return parent
            parent = parents[parent]
        return -1

    out: list[Triangle] = []
    for outer_index, outer in enumerate(rings):
        if not filled[outer_index]:
            continue
        polygon_rings = [outer]
        polygon_rings.extend(
            ring
            for ring_index, ring in enumerate(rings)
            if not filled[ring_index]
            and nearest_filled_ancestor(ring_index) == outer_index
        )
        vertices: list[tuple[float, float]] = []
        ring_ends: list[int] = []
        for ring in polygon_rings:
            vertices.extend(ring)
            ring_ends.append(len(vertices))
        if len(vertices) < 3:
            continue
        try:
            vertex_array = np.asarray(vertices, dtype=np.float64)
            ring_array = np.asarray(ring_ends, dtype=np.uint32)
            indices = earcut.triangulate_float64(vertex_array, ring_array)
        except Exception:
            return None
        for i in range(0, len(indices) - 2, 3):
            pa = vertices[int(indices[i])]
            pb = vertices[int(indices[i + 1])]
            pc = vertices[int(indices[i + 2])]
            if abs(contour_area([pa, pb, pc])) <= 1e-5:
                continue
            native = ((pa[0], -pa[1]), (pb[0], -pb[1]), (pc[0], -pc[1]))
            out.append(Triangle(segment_id, fill_index, native))
    return out


def triangulate_contours_delaunay(
    transformed: list[list[tuple[float, float]]],
    segment_id: int,
    fill_index: int,
) -> list[Triangle]:
    try:
        from matplotlib.tri import Triangulation
    except Exception as exc:
        raise SwflError("SVG path/text triangulation requires matplotlib") from exc

    points: list[tuple[float, float]] = []
    seen: set[tuple[int, int]] = set()
    for contour in transformed:
        for x, y in contour:
            key = (round(x * 10000), round(y * 10000))
            if key in seen:
                continue
            seen.add(key)
            points.append((x, y))
    if len(points) < 3:
        return []

    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    try:
        tri = Triangulation(xs, ys)
    except Exception:
        return fan_triangles(transformed[0], segment_id, fill_index)

    out: list[Triangle] = []
    for a, b, c in tri.triangles:
        pa, pb, pc = points[int(a)], points[int(b)], points[int(c)]
        samples = [
            ((pa[0] + pb[0] + pc[0]) / 3.0, (pa[1] + pb[1] + pc[1]) / 3.0),
            ((pa[0] + pb[0]) / 2.0, (pa[1] + pb[1]) / 2.0),
            ((pb[0] + pc[0]) / 2.0, (pb[1] + pc[1]) / 2.0),
            ((pc[0] + pa[0]) / 2.0, (pc[1] + pa[1]) / 2.0),
        ]
        if not all(point_in_compound(x, y, transformed) for x, y in samples):
            continue
        native = ((pa[0], -pa[1]), (pb[0], -pb[1]), (pc[0], -pc[1]))
        out.append(Triangle(segment_id, fill_index, native))
    return out


def triangulate_contours(
    contours: list[list[tuple[float, float]]],
    segment_id: int,
    fill_index: int,
    matrix: Transform,
    fill_rule: str = "nonzero",
) -> list[Triangle]:
    transformed = [
        [apply_mat(matrix, x, y) for x, y in contour]
        for contour in clean_contours(contours)
    ]
    transformed = clean_contours(transformed)
    if not transformed:
        return []

    earcut_triangles = triangulate_contours_earcut(
        transformed, segment_id, fill_index, fill_rule
    )
    if earcut_triangles is not None:
        return earcut_triangles
    return triangulate_contours_delaunay(transformed, segment_id, fill_index)


def svg_element_contours(node: ET.Element) -> list[list[tuple[float, float]]]:
    tag = local_name(node.tag).lower()
    if tag == "path":
        return path_contours(node.attrib.get("d", ""))
    if tag == "rect":
        return rect_contours(node)
    if tag == "circle":
        return ellipse_contours(node, circle=True)
    if tag == "ellipse":
        return ellipse_contours(node, circle=False)
    if tag == "text":
        return text_contours(node)
    return []


def read_svg_triangles(svg_path: Path) -> dict[int, list[Triangle]]:
    root = ET.parse(svg_path).getroot()
    css_classes = collect_css_classes(root)
    grouped: dict[int, list[Triangle]] = {}
    root_segments = parse_int_list(root.attrib.get("data-swfl-segments"))
    file_segment: int | None = None
    match = re.search(r"segment[_-]?(\d+)", svg_path.stem)
    if match:
        file_segment = int(match.group(1))
    default_segment = (
        file_segment if file_segment is not None else node_segment(root, None)
    )
    if default_segment is None:
        default_segment = node_segment(root, None)
    if default_segment is None and root_segments:
        default_segment = root_segments[0]
    used_default_segment = False

    def add_triangles(segment_id: int | None, triangles: list[Triangle]) -> None:
        nonlocal used_default_segment
        if segment_id is None:
            if default_segment is None:
                return
            segment_id = default_segment
            used_default_segment = True
        grouped.setdefault(segment_id, []).extend(
            Triangle(segment_id, triangle.fill_index, triangle.points)
            for triangle in triangles
        )

    def visit(
        node: ET.Element,
        matrix: Transform,
        inherited_segment: int | None,
        inherited_fill: int,
        inherited_fill_rule: str,
    ) -> None:
        current_matrix = mat_mul(matrix, parse_transform(node.attrib.get("transform")))
        segment_id = (
            file_segment
            if file_segment is not None
            else node_segment(node, inherited_segment)
        )
        fill_index = node_fill_index(node, inherited_fill)
        fill_rule = (
            attr_value(node, "fill-rule", inherited_fill_rule, css_classes)
            or inherited_fill_rule
        )
        tag = local_name(node.tag).lower()

        if is_drawn(node, css_classes) and tag in {"polygon", "polyline"}:
            raw_points = [
                apply_mat(current_matrix, x, y)
                for x, y in parse_points(node.attrib.get("points", ""))
            ]
            add_triangles(
                segment_id,
                fan_triangles(
                    raw_points, segment_id if segment_id is not None else -1, fill_index
                ),
            )
        elif is_drawn(node, css_classes) and tag in {
            "path",
            "rect",
            "circle",
            "ellipse",
            "text",
        }:
            triangles = triangulate_contours(
                svg_element_contours(node),
                segment_id if segment_id is not None else -1,
                fill_index,
                current_matrix,
                fill_rule,
            )
            add_triangles(segment_id, triangles)

        for child in list(node):
            visit(child, current_matrix, segment_id, fill_index, fill_rule)

    visit(root, (1.0, 0.0, 0.0, 1.0, 0.0, 0.0), None, -1, "nonzero")
    return grouped


def svg_single_segment_metadata(
    svg_path: Path,
    replacement: dict[int, list[Triangle]],
) -> int | None:
    try:
        root = ET.parse(svg_path).getroot()
    except ET.ParseError:
        return None

    segment_id: int | None = None
    match = re.search(r"segment[_-]?(\d+)", svg_path.stem)
    if match:
        segment_id = int(match.group(1))
    kind = root.attrib.get("data-swfl-kind", "").lower()
    is_segment_export = (
        kind == "segment"
        or segment_id is not None
        or "data-swfl-segment" in root.attrib
    )
    if segment_id is None:
        segment_id = node_segment(root, None)
    if segment_id is None and is_segment_export and len(replacement) == 1:
        segment_id = next(iter(replacement))
    if segment_id is None or not is_segment_export or set(replacement) != {segment_id}:
        return None
    return segment_id


def maybe_align_segment_replacement(
    svg_path: Path,
    replacement: dict[int, list[Triangle]],
    original_groups: dict[int, list[Triangle]],
) -> dict[int, list[Triangle]]:
    segment_id = svg_single_segment_metadata(svg_path, replacement)
    if segment_id is None:
        return replacement
    original = original_groups.get(segment_id)
    if not original:
        return replacement
    source_bounds = triangle_bounds(replacement[segment_id])
    target_bounds = triangle_bounds(original)
    if all(abs(source_bounds[i] - target_bounds[i]) <= 1e-4 for i in range(4)):
        return replacement
    return {
        segment_id: transform_triangles_bounds(
            replacement[segment_id],
            source_bounds,
            target_bounds,
        )
    }


def original_triangle_groups(mesh: Mesh) -> dict[int, list[Triangle]]:
    return {
        segment_id: triangles_for_segments(mesh, [segment_id])
        for segment_id in range(len(mesh.segments))
    }


def import_svg_groups(mesh: Mesh, input_path: Path) -> dict[int, list[Triangle]]:
    groups = original_triangle_groups(mesh)
    if input_path.is_file():
        replacement = read_svg_triangles(input_path)
        replacement = maybe_align_segment_replacement(input_path, replacement, groups)
        for segment_id, triangles in replacement.items():
            groups[segment_id] = triangles
        return groups

    if not input_path.is_dir():
        raise SwflError(f"SVG input does not exist: {input_path}")

    candidates = sorted((input_path / "shapes").glob("*.svg"))
    unassigned = input_path / "unassigned_segments.svg"
    if unassigned.exists():
        candidates.append(unassigned)
    unassigned_dir = input_path / "unassigned_segments"
    if unassigned_dir.is_dir():
        candidates.extend(sorted(unassigned_dir.glob("*.svg")))
    candidates.sort(key=lambda p: p.stat().st_mtime)
    if not candidates:
        full = input_path / "full.svg"
        if full.exists():
            replacement = read_svg_triangles(full)
            return replacement
        candidates = sorted(input_path.rglob("*.svg"))

    touched = 0
    for svg_path in candidates:
        replacement = read_svg_triangles(svg_path)
        replacement = maybe_align_segment_replacement(svg_path, replacement, groups)
        for segment_id, triangles in replacement.items():
            groups[segment_id] = triangles
            touched += 1
    if touched == 0:
        raise SwflError(f"no SWFL polygon data found in {input_path}")
    return groups


def build_geometry(
    mesh: Mesh, groups: dict[int, list[Triangle]]
) -> tuple[bytes, bytes, bytes]:
    new_segments: list[Segment] = []
    new_vertices: list[tuple[float, float]] = []
    new_indices: list[int] = []

    for segment_id, old_segment in enumerate(mesh.segments):
        triangles = groups.get(segment_id, [])
        vertex_start = len(new_vertices)
        index_start = len(new_indices)
        fill_index = old_segment.fill_index
        if triangles and triangles[0].fill_index >= 0:
            fill_index = triangles[0].fill_index
        for triangle in triangles:
            local_base = len(new_vertices) - vertex_start
            for point in triangle.points:
                new_vertices.append(point)
            new_indices.extend([local_base, local_base + 1, local_base + 2])
        triangle_count = len(triangles)
        if triangle_count > 0xFFFF:
            raise SwflError(
                f"segment {segment_id} has too many triangles: {triangle_count}"
            )
        if vertex_start > 0xFFFF or index_start > 0xFFFF:
            raise SwflError("SWFL buffer grew past u16 segment offsets")
        new_segments.append(
            Segment(
                fill_index=fill_index,
                triangle_count=triangle_count,
                vertex_start=vertex_start,
                index_start=index_start,
                unk0=old_segment.unk0,
                unk1=old_segment.unk1,
            )
        )

    if len(new_vertices) > 0xFFFFFFFF or len(new_indices) > 0xFFFFFFFF:
        raise SwflError("geometry buffers are too large")

    segment_bytes = bytearray()
    segment_bytes += struct.pack("<I", len(new_segments))
    for segment in new_segments:
        segment_bytes += struct.pack(
            "<IHHHHH",
            segment.fill_index,
            segment.triangle_count,
            segment.vertex_start,
            segment.index_start,
            segment.unk0,
            segment.unk1,
        )

    vertex_bytes = bytearray()
    vertex_bytes += struct.pack("<II", 1, len(new_vertices))
    for x, y in new_vertices:
        vertex_bytes += struct.pack("<ff", x, y)

    index_bytes = bytearray()
    index_bytes += struct.pack("<II", 1, len(new_indices))
    for value in new_indices:
        if value > 0xFFFF:
            raise SwflError("a segment has more than 65535 local vertices")
        index_bytes += struct.pack("<H", value)

    return bytes(segment_bytes), bytes(vertex_bytes), bytes(index_bytes)


def rebuild_swfl(mesh: Mesh, groups: dict[int, list[Triangle]], output: Path) -> None:
    segment_bytes, vertex_bytes, index_bytes = build_geometry(mesh, groups)
    o = mesh.offsets
    data = (
        mesh.data[: o.segments_start]
        + segment_bytes
        + mesh.data[o.segments_end : o.vertices_start]
        + vertex_bytes
        + mesh.data[o.vertices_end : o.indices_start]
        + index_bytes
        + mesh.data[o.indices_end :]
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(data)
    parse_mesh(output)


def command_import(args: argparse.Namespace) -> int:
    source = args.source.resolve()
    svg_input = args.svg_input.resolve()
    output = args.output.resolve()
    mesh = parse_mesh(source)
    groups = import_svg_groups(mesh, svg_input)
    rebuild_swfl(mesh, groups, output)
    triangle_count = sum(len(v) for v in groups.values())
    print(f"Imported SVG mesh data: {svg_input}")
    print(f"Wrote SWFL: {output}")
    print(f"  segments: {len(mesh.segments)}")
    print(f"  triangles: {triangle_count}")
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export/import SWFL vector meshes as SVG; import triangulates SVG paths/text."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    export = sub.add_parser(
        "export", help="Export one SWFL to full/per-shape SVG files."
    )
    export.add_argument("input", type=Path, help="Input .swfl file or folder.")
    export.add_argument("output", type=Path, help="Output folder.")
    export.set_defaults(func=command_export)

    imp = sub.add_parser(
        "import",
        help="Import exported or edited SVG into a SWFL copy, with automatic triangulation.",
    )
    imp.add_argument("source", type=Path, help="Original source .swfl.")
    imp.add_argument("svg_input", type=Path, help="Exported full.svg or export folder.")
    imp.add_argument("output", type=Path, help="Output .swfl file.")
    imp.set_defaults(func=command_import)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except SwflError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
