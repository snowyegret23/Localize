#!/usr/bin/env python3
from __future__ import annotations

import base64
import csv
import json
import struct
import sys
from pathlib import Path
from typing import Any, Iterable

import lz4.block
import msgpack

SCHEMA = "aitsf2-code-json-v2"
LEGACY_SCHEMA = "aitsf2-code-json-v1"
LZ4_BLOCK_ARRAY_EXT_CODE = 98
DEFAULT_BLOCK_SIZE = 0x7FFF
DEFAULT_NAME_CSV = "AITSF2_CodeName.csv"


class MapNode(list):
    """Marker type used to preserve map entries with non-string keys."""


class _MsgpackCursor:
    def __init__(self, payload: bytes) -> None:
        self._u = msgpack.Unpacker(raw=False, strict_map_key=False, timestamp=0)
        self._u.feed(payload)

    def unpack(self) -> Any:
        try:
            return self._u.unpack()
        except msgpack.OutOfData as exc:
            raise ValueError("Unexpected end of payload while reading value") from exc

    def read_map_header(self) -> int:
        try:
            return int(self._u.read_map_header())
        except msgpack.OutOfData as exc:
            raise ValueError("Unexpected end of payload while reading map header") from exc
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"Expected map header: {exc}") from exc

    def read_array_header(self) -> int:
        try:
            return int(self._u.read_array_header())
        except msgpack.OutOfData as exc:
            raise ValueError("Unexpected end of payload while reading array header") from exc
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"Expected array header: {exc}") from exc

    def ensure_eof(self) -> None:
        try:
            extra = self._u.unpack()
        except msgpack.OutOfData:
            return
        raise ValueError(f"Trailing data after parsed payload (next type: {type(extra).__name__})")


def _iter_map_entries(node: Any) -> list[tuple[Any, Any]]:
    if isinstance(node, MapNode):
        entries: list[tuple[Any, Any]] = []
        for idx, entry in enumerate(node):
            if not isinstance(entry, (list, tuple)) or len(entry) != 2:
                raise TypeError(f"MapNode entry at index {idx} must be [key, value]")
            entries.append((entry[0], entry[1]))
        return entries
    if isinstance(node, dict):
        return list(node.items())
    raise TypeError(f"Expected map-like value, got {type(node)!r}")


def _unpack_msgpack_stream(payload: bytes) -> list[Any]:
    unpacker = msgpack.Unpacker(
        raw=False,
        strict_map_key=False,
        object_pairs_hook=MapNode,
        timestamp=0,
    )
    unpacker.feed(payload)
    return list(unpacker)


def _pack_msgpack(value: Any) -> bytes:
    packer = msgpack.Packer(use_bin_type=True)
    parts: list[bytes] = []

    def emit(node: Any) -> None:
        if isinstance(node, MapNode):
            entries = _iter_map_entries(node)
            parts.append(packer.pack_map_header(len(entries)))
            for key, val in entries:
                emit(key)
                emit(val)
            return

        if isinstance(node, dict):
            parts.append(packer.pack_map_header(len(node)))
            for key, val in node.items():
                emit(key)
                emit(val)
            return

        if isinstance(node, (list, tuple)):
            parts.append(packer.pack_array_header(len(node)))
            for item in node:
                emit(item)
            return

        if isinstance(node, msgpack.ExtType):
            parts.append(packer.pack_ext_type(node.code, node.data))
            return

        if isinstance(node, msgpack.Timestamp):
            parts.append(packer.pack(node))
            return

        if isinstance(node, bytearray):
            parts.append(packer.pack(bytes(node)))
            return

        if isinstance(node, (str, bytes, int, float, bool)) or node is None:
            parts.append(packer.pack(node))
            return

        raise TypeError(f"Unsupported value type for msgpack packing: {type(node)!r}")

    emit(value)
    return b"".join(parts)


def _pack_msgpack_stream(items: list[Any]) -> bytes:
    return b"".join(_pack_msgpack(item) for item in items)


def _parse_size_payload(size_payload: bytes, expected_blocks: int) -> list[int]:
    unpacker = msgpack.Unpacker(raw=False, strict_map_key=False)
    unpacker.feed(size_payload)
    sizes = list(unpacker)
    if len(sizes) != expected_blocks:
        raise ValueError(
            f"Block size count mismatch: expected {expected_blocks}, got {len(sizes)}"
        )
    parsed: list[int] = []
    for idx, size in enumerate(sizes):
        if not isinstance(size, int):
            raise TypeError(f"Block size at index {idx} is not int: {type(size)!r}")
        if size < 0:
            raise ValueError(f"Block size at index {idx} is negative: {size}")
        parsed.append(size)
    return parsed


def _build_size_payload(sizes: Iterable[int]) -> bytes:
    return b"".join(msgpack.packb(int(size), use_bin_type=True) for size in sizes)


def _decode_lz4_block_array(segment: Any) -> tuple[int, list[int], bytes]:
    if not isinstance(segment, list) or len(segment) < 2:
        raise TypeError("Segment must be a list with [ExtType, block1, ...]")

    ext = segment[0]
    blocks = segment[1:]
    if not isinstance(ext, msgpack.ExtType):
        raise TypeError("Segment[0] is not MessagePack ExtType")

    sizes = _parse_size_payload(ext.data, len(blocks))
    decompressed_chunks: list[bytes] = []
    for idx, (block, size) in enumerate(zip(blocks, sizes)):
        if not isinstance(block, (bytes, bytearray)):
            raise TypeError(f"Block at index {idx} is not bytes")
        try:
            chunk = lz4.block.decompress(bytes(block), uncompressed_size=size)
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"Failed to decompress block {idx}: {exc}") from exc
        if len(chunk) != size:
            raise ValueError(
                f"Block {idx} decompressed size mismatch: expected {size}, got {len(chunk)}"
            )
        decompressed_chunks.append(chunk)

    return ext.code, sizes, b"".join(decompressed_chunks)


def _split_payload(payload: bytes, block_sizes_hint: list[int] | None) -> list[bytes]:
    if block_sizes_hint:
        if all(isinstance(x, int) and x >= 0 for x in block_sizes_hint) and sum(
            block_sizes_hint
        ) == len(payload):
            chunks: list[bytes] = []
            offset = 0
            for size in block_sizes_hint:
                chunks.append(payload[offset : offset + size])
                offset += size
            return chunks

    if not payload:
        return [b""]

    return [
        payload[i : i + DEFAULT_BLOCK_SIZE]
        for i in range(0, len(payload), DEFAULT_BLOCK_SIZE)
    ]


def _encode_lz4_block_array(
    payload: bytes, ext_code: int, block_sizes_hint: list[int] | None
) -> list[Any]:
    chunks = _split_payload(payload, block_sizes_hint)
    sizes = [len(chunk) for chunk in chunks]
    compressed = [
        lz4.block.compress(chunk, store_size=False, mode="high_compression")
        for chunk in chunks
    ]
    ext = msgpack.ExtType(ext_code, _build_size_payload(sizes))
    return [ext, *compressed]


def _b64_encode(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _b64_decode(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"), validate=True)


def _to_json_node(node: Any) -> Any:
    if isinstance(node, MapNode):
        entries = _iter_map_entries(node)
        if all(isinstance(k, str) for k, _ in entries):
            out: dict[str, Any] = {}
            for key, val in entries:
                out[key] = _to_json_node(val)
            return out
        return {"$map": [[_to_json_node(k), _to_json_node(v)] for k, v in entries]}

    if isinstance(node, dict):
        if all(isinstance(k, str) for k in node.keys()):
            return {k: _to_json_node(v) for k, v in node.items()}
        return {
            "$map": [[_to_json_node(k), _to_json_node(v)] for k, v in node.items()]
        }

    if isinstance(node, list):
        return [_to_json_node(item) for item in node]

    if isinstance(node, tuple):
        return [_to_json_node(item) for item in node]

    if isinstance(node, msgpack.ExtType):
        return {"$ext": {"code": int(node.code), "data": _b64_encode(node.data)}}

    if isinstance(node, msgpack.Timestamp):
        return {
            "$timestamp": {
                "seconds": int(node.seconds),
                "nanoseconds": int(node.nanoseconds),
            }
        }

    if isinstance(node, (bytes, bytearray)):
        return {"$bin": _b64_encode(bytes(node))}

    if isinstance(node, (str, int, float, bool)) or node is None:
        return node

    raise TypeError(f"Unsupported value type for JSON conversion: {type(node)!r}")


def _from_json_node(node: Any) -> Any:
    if isinstance(node, dict):
        if set(node.keys()) == {"$bin"}:
            raw = node["$bin"]
            if not isinstance(raw, str):
                raise TypeError("$bin must be a base64 string")
            return _b64_decode(raw)

        if set(node.keys()) == {"$ext"}:
            ext_info = node["$ext"]
            if not isinstance(ext_info, dict):
                raise TypeError("$ext must be an object")
            if "code" not in ext_info or "data" not in ext_info:
                raise TypeError("$ext object must contain code and data")
            code = int(ext_info["code"])
            data = ext_info["data"]
            if not isinstance(data, str):
                raise TypeError("$ext.data must be a base64 string")
            return msgpack.ExtType(code, _b64_decode(data))

        if set(node.keys()) == {"$map"}:
            entries = node["$map"]
            if not isinstance(entries, list):
                raise TypeError("$map must be a list")
            out = MapNode()
            for idx, entry in enumerate(entries):
                if not isinstance(entry, list) or len(entry) != 2:
                    raise TypeError(f"$map entry at index {idx} must be [key, value]")
                out.append([_from_json_node(entry[0]), _from_json_node(entry[1])])
            return out

        if set(node.keys()) == {"$timestamp"}:
            stamp = node["$timestamp"]
            if not isinstance(stamp, dict):
                raise TypeError("$timestamp must be an object")
            if "seconds" not in stamp or "nanoseconds" not in stamp:
                raise TypeError("$timestamp must contain seconds and nanoseconds")
            return msgpack.Timestamp(
                int(stamp["seconds"]),
                int(stamp["nanoseconds"]),
            )

        return {key: _from_json_node(val) for key, val in node.items()}

    if isinstance(node, list):
        return [_from_json_node(item) for item in node]

    if isinstance(node, (str, int, float, bool)) or node is None:
        return node

    raise TypeError(f"Unsupported JSON node type: {type(node)!r}")


def _parse_codes_blob(cursor: _MsgpackCursor) -> tuple[bytes, int]:
    code_count = cursor.read_array_header()
    packer = msgpack.Packer(use_bin_type=True)
    parts: list[bytes] = [packer.pack_array_header(code_count)]
    for idx in range(code_count):
        opcode = cursor.unpack()
        index = cursor.unpack()
        count = cursor.unpack()
        extra = cursor.unpack()
        if not all(isinstance(x, int) for x in (opcode, index, count, extra)):
            raise TypeError(f"Code entry {idx} must contain four integer values")
        opcode_i = int(opcode)
        index_i = int(index)
        count_i = int(count)
        extra_i = int(extra)
        if not 0 <= opcode_i <= 255:
            raise ValueError(f"Code entry {idx} opcode out of range: {opcode_i}")
        if not -32768 <= index_i <= 32767:
            raise ValueError(f"Code entry {idx} index out of range: {index_i}")
        if not -(2**31) <= count_i <= (2**31 - 1):
            raise ValueError(f"Code entry {idx} count out of range: {count_i}")
        if not -128 <= extra_i <= 127:
            raise ValueError(f"Code entry {idx} extra out of range: {extra_i}")
        parts.append(_pack_uint8(opcode_i))
        parts.append(_pack_int16(index_i))
        parts.append(_pack_int32(count_i))
        parts.append(_pack_int8(extra_i))
    return b"".join(parts), code_count


def _parse_method(cursor: _MsgpackCursor) -> dict[str, Any]:
    count = cursor.read_map_header()
    out: dict[str, Any] = {}
    for _ in range(count):
        key = cursor.unpack()
        if key == "codes":
            blob, code_count = _parse_codes_blob(cursor)
            out["codes_b64"] = _b64_encode(blob)
            out["codes_count"] = code_count
        else:
            out[key] = cursor.unpack()
    return out


def _parse_methods(cursor: _MsgpackCursor) -> dict[Any, Any]:
    count = cursor.read_map_header()
    out: dict[Any, Any] = {}
    for _ in range(count):
        method_name = cursor.unpack()
        out[method_name] = _parse_method(cursor)
    return out


def _parse_type_info(cursor: _MsgpackCursor) -> dict[str, Any]:
    count = cursor.read_map_header()
    out: dict[str, Any] = {}
    for _ in range(count):
        key = cursor.unpack()
        if key == "methods":
            out["methods"] = _parse_methods(cursor)
        else:
            out[key] = cursor.unpack()
    return out


def _parse_script_payload(payload: bytes) -> dict[str, Any]:
    cursor = _MsgpackCursor(payload)
    count = cursor.read_map_header()
    out: dict[str, Any] = {}
    for _ in range(count):
        key = cursor.unpack()
        if key == "typeInfos":
            type_info_count = cursor.read_map_header()
            type_infos: dict[Any, Any] = {}
            for _ in range(type_info_count):
                type_name = cursor.unpack()
                type_infos[type_name] = _parse_type_info(cursor)
            out["typeInfos"] = type_infos
        else:
            out[key] = cursor.unpack()
    cursor.ensure_eof()
    return out


def _normalize_code_item(item: Any, idx: int) -> tuple[int, int, int, int]:
    if isinstance(item, dict):
        required = ("opcode", "index", "count", "extra")
        missing = [k for k in required if k not in item]
        if missing:
            raise ValueError(f"Code item {idx} missing keys: {', '.join(missing)}")
        opcode = item["opcode"]
        index = item["index"]
        count = item["count"]
        extra = item["extra"]
    elif isinstance(item, MapNode):
        mapping: dict[str, Any] = {}
        for entry in item:
            if (
                isinstance(entry, (list, tuple))
                and len(entry) == 2
                and isinstance(entry[0], str)
            ):
                mapping[entry[0]] = entry[1]
        return _normalize_code_item(mapping, idx)
    elif isinstance(item, (list, tuple)) and len(item) == 4:
        opcode, index, count, extra = item
    else:
        raise TypeError(
            f"Code item {idx} must be object with opcode/index/count/extra or [4] list"
        )

    if not all(isinstance(x, int) for x in (opcode, index, count, extra)):
        raise TypeError(f"Code item {idx} fields must all be integers")

    opcode_i = int(opcode)
    index_i = int(index)
    count_i = int(count)
    extra_i = int(extra)

    if not 0 <= opcode_i <= 255:
        raise ValueError(f"Code item {idx} opcode out of range (0..255): {opcode_i}")
    if not -32768 <= index_i <= 32767:
        raise ValueError(f"Code item {idx} index out of range (-32768..32767): {index_i}")
    if not -(2**31) <= count_i <= (2**31 - 1):
        raise ValueError(
            f"Code item {idx} count out of range (-2147483648..2147483647): {count_i}"
        )
    if not -128 <= extra_i <= 127:
        raise ValueError(f"Code item {idx} extra out of range (-128..127): {extra_i}")

    return opcode_i, index_i, count_i, extra_i


def _pack_uint8(value: int) -> bytes:
    return bytes((0xCC, value & 0xFF))


def _pack_int8(value: int) -> bytes:
    return struct.pack(">Bb", 0xD0, value)


def _pack_int16(value: int) -> bytes:
    return struct.pack(">Bh", 0xD1, value)


def _pack_int32(value: int) -> bytes:
    return struct.pack(">Bi", 0xD2, value)


def _pack_float32(value: float) -> bytes:
    return struct.pack(">Bf", 0xCA, float(value))


def _emit_codes(codes: Any, packer: msgpack.Packer, parts: list[bytes]) -> None:
    if not isinstance(codes, list):
        raise TypeError("Method 'codes' must be a list")

    if codes and all(isinstance(x, int) for x in codes):
        if len(codes) % 4 != 0:
            raise ValueError(
                "Flat integer 'codes' list length must be multiple of 4 (opcode,index,count,extra)"
            )
        parts.append(packer.pack_array_header(len(codes) // 4))
        for idx in range(0, len(codes), 4):
            opcode, index, count, extra = _normalize_code_item(codes[idx : idx + 4], idx // 4)
            parts.append(_pack_uint8(opcode))
            parts.append(_pack_int16(index))
            parts.append(_pack_int32(count))
            parts.append(_pack_int8(extra))
        return

    parts.append(packer.pack_array_header(len(codes)))
    for idx, item in enumerate(codes):
        opcode, index, count, extra = _normalize_code_item(item, idx)
        parts.append(_pack_uint8(opcode))
        parts.append(_pack_int16(index))
        parts.append(_pack_int32(count))
        parts.append(_pack_int8(extra))


def _pack_msgpack_for_values(value: Any) -> bytes:
    packer = msgpack.Packer(use_bin_type=True)
    parts: list[bytes] = []

    def emit(node: Any) -> None:
        if isinstance(node, bool):
            parts.append(packer.pack(node))
            return

        if isinstance(node, int):
            if not -(2**31) <= node <= (2**31 - 1):
                raise ValueError(f"Integer in values is out of Int32 range: {node}")
            parts.append(_pack_int32(int(node)))
            return

        if isinstance(node, MapNode):
            entries = _iter_map_entries(node)
            parts.append(packer.pack_map_header(len(entries)))
            for key, val in entries:
                emit(key)
                emit(val)
            return

        if isinstance(node, dict):
            parts.append(packer.pack_map_header(len(node)))
            for key, val in node.items():
                emit(key)
                emit(val)
            return

        if isinstance(node, (list, tuple)):
            parts.append(packer.pack_array_header(len(node)))
            for item in node:
                emit(item)
            return

        if isinstance(node, msgpack.ExtType):
            parts.append(packer.pack_ext_type(node.code, node.data))
            return

        if isinstance(node, msgpack.Timestamp):
            parts.append(packer.pack(node))
            return

        if isinstance(node, bytearray):
            parts.append(packer.pack(bytes(node)))
            return

        if isinstance(node, float):
            parts.append(_pack_float32(node))
            return

        if isinstance(node, (str, bytes)) or node is None:
            parts.append(packer.pack(node))
            return

        raise TypeError(f"Unsupported value type in method values: {type(node)!r}")

    emit(value)
    return b"".join(parts)


def _emit_values(values_obj: Any, packer: msgpack.Packer, parts: list[bytes]) -> None:
    if not isinstance(values_obj, list):
        raise TypeError("Method 'values' must be a list")
    parts.append(packer.pack_array_header(len(values_obj)))
    for value in values_obj:
        parts.append(_pack_msgpack_for_values(value))


def _emit_method(method_obj: Any, packer: msgpack.Packer, parts: list[bytes]) -> None:
    raw_entries = _iter_map_entries(method_obj)
    entries: list[tuple[Any, Any, str]] = []
    have_codes = False
    for key, val in raw_entries:
        if key == "codes_count":
            continue
        if key == "codes_b64":
            entries.append(("codes", val, "codes_b64"))
            have_codes = True
            continue
        if key == "codes" and not have_codes:
            entries.append(("codes", val, "codes_struct"))
            continue
        if key == "values":
            entries.append((key, val, "values"))
            continue
        entries.append((key, val, "generic"))

    parts.append(packer.pack_map_header(len(entries)))
    for key, val, mode in entries:
        parts.append(_pack_msgpack(key))
        if mode == "codes_b64":
            if not isinstance(val, str):
                raise TypeError("codes_b64 must be a base64 string")
            parts.append(_b64_decode(val))
        elif mode == "codes_struct":
            _emit_codes(val, packer, parts)
        elif mode == "values":
            _emit_values(val, packer, parts)
        else:
            parts.append(_pack_msgpack(val))


def _emit_methods(methods_obj: Any, packer: msgpack.Packer, parts: list[bytes]) -> None:
    entries = _iter_map_entries(methods_obj)
    parts.append(packer.pack_map_header(len(entries)))
    for key, method_obj in entries:
        parts.append(_pack_msgpack(key))
        _emit_method(method_obj, packer, parts)


def _emit_type_info(type_info_obj: Any, packer: msgpack.Packer, parts: list[bytes]) -> None:
    entries = _iter_map_entries(type_info_obj)
    parts.append(packer.pack_map_header(len(entries)))
    for key, val in entries:
        parts.append(_pack_msgpack(key))
        if key == "methods":
            _emit_methods(val, packer, parts)
        else:
            parts.append(_pack_msgpack(val))


def _emit_type_infos(type_infos_obj: Any, packer: msgpack.Packer, parts: list[bytes]) -> None:
    entries = _iter_map_entries(type_infos_obj)
    parts.append(packer.pack_map_header(len(entries)))
    for type_name, type_info_obj in entries:
        parts.append(_pack_msgpack(type_name))
        _emit_type_info(type_info_obj, packer, parts)


def _encode_script_payload(script_obj: Any) -> bytes:
    entries = _iter_map_entries(script_obj)
    packer = msgpack.Packer(use_bin_type=True)
    parts: list[bytes] = []
    parts.append(packer.pack_map_header(len(entries)))
    for key, val in entries:
        parts.append(_pack_msgpack(key))
        if key == "typeInfos":
            _emit_type_infos(val, packer, parts)
        else:
            parts.append(_pack_msgpack(val))
    return b"".join(parts)


def code_to_json(src_path: Path, dst_path: Path, include_raw: bool = False) -> None:
    segments_raw = list(
        msgpack.Unpacker(
            src_path.open("rb"),
            raw=False,
            strict_map_key=False,
            timestamp=0,
        )
    )
    if not segments_raw:
        raise ValueError("Input .code has no MessagePack segments")

    segments_json: list[dict[str, Any]] = []
    for idx, segment_raw in enumerate(segments_raw):
        ext_code, block_sizes, payload = _decode_lz4_block_array(segment_raw)
        segment_json: dict[str, Any] = {
            "index": idx,
            "ext_code": ext_code,
            "block_sizes": block_sizes,
            "payload_len": len(payload),
        }

        if idx == 0:
            header_obj = msgpack.unpackb(
                payload,
                raw=False,
                strict_map_key=False,
                object_pairs_hook=MapNode,
                timestamp=0,
            )
            segment_json["kind"] = "header"
            segment_json["header"] = _to_json_node(header_obj)
        elif idx == 1:
            script_obj = _parse_script_payload(payload)
            segment_json["kind"] = "script"
            segment_json["script"] = _to_json_node(script_obj)
        else:
            stream = _unpack_msgpack_stream(payload)
            segment_json["kind"] = "stream"
            segment_json["stream"] = [_to_json_node(item) for item in stream]

        if include_raw:
            segment_json["payload_b64"] = _b64_encode(payload)

        segments_json.append(segment_json)

    doc = {
        "$schema": SCHEMA,
        "segment_count": len(segments_json),
        "segments": segments_json,
    }
    dst_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")


def _payload_from_segment_json(idx: int, segment: dict[str, Any]) -> bytes:
    kind = segment.get("kind")
    if kind == "header" or "header" in segment:
        return _pack_msgpack(_from_json_node(segment["header"]))

    if kind == "script" or "script" in segment:
        return _encode_script_payload(_from_json_node(segment["script"]))

    if kind == "stream" or "stream" in segment:
        stream_nodes = segment["stream"]
        if not isinstance(stream_nodes, list):
            raise TypeError(f"Segment {idx} stream must be a list")
        return _pack_msgpack_stream([_from_json_node(item) for item in stream_nodes])

    # Legacy compatibility path (v1 output)
    if "payload_b64" in segment:
        payload_b64 = segment["payload_b64"]
        if not isinstance(payload_b64, str):
            raise TypeError(f"Segment {idx} payload_b64 must be a base64 string")
        payload = _b64_decode(payload_b64)

        if bool(segment.get("use_stream_preview")) and "stream_preview" in segment:
            stream_nodes = segment["stream_preview"]
            if not isinstance(stream_nodes, list):
                raise TypeError(f"Segment {idx} stream_preview must be a list")
            payload = _pack_msgpack_stream([_from_json_node(item) for item in stream_nodes])

        if idx == 0 and bool(segment.get("use_header_preview")) and "header_preview" in segment:
            payload = _pack_msgpack(_from_json_node(segment["header_preview"]))

        return payload

    if "stream_preview" in segment:
        stream_nodes = segment["stream_preview"]
        if not isinstance(stream_nodes, list):
            raise TypeError(f"Segment {idx} stream_preview must be a list")
        return _pack_msgpack_stream([_from_json_node(item) for item in stream_nodes])

    if "data" in segment:
        return _pack_msgpack(_from_json_node(segment["data"]))

    raise ValueError(
        f"Segment {idx} missing supported payload field "
        f"(header/script/stream/payload_b64/data)"
    )


def json_to_code(src_path: Path, dst_path: Path) -> None:
    doc = json.loads(src_path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        raise TypeError("JSON root must be an object")

    schema = doc.get("$schema")
    if schema not in {SCHEMA, LEGACY_SCHEMA}:
        raise ValueError(
            f"Unsupported or missing $schema (expected {SCHEMA} or {LEGACY_SCHEMA})"
        )

    segments = doc.get("segments")
    if not isinstance(segments, list) or not segments:
        raise ValueError("JSON must contain a non-empty segments list")

    out = bytearray()
    for idx, segment in enumerate(segments):
        if not isinstance(segment, dict):
            raise TypeError(f"Segment {idx} must be an object")
        ext_code = int(segment.get("ext_code", LZ4_BLOCK_ARRAY_EXT_CODE))
        block_sizes = segment.get("block_sizes")
        if block_sizes is not None:
            if not isinstance(block_sizes, list) or not all(
                isinstance(x, int) and x >= 0 for x in block_sizes
            ):
                raise TypeError(f"Segment {idx} block_sizes must be a list[int >= 0]")

        payload = _payload_from_segment_json(idx, segment)
        segment_raw = _encode_lz4_block_array(payload, ext_code, block_sizes)
        out.extend(msgpack.packb(segment_raw, use_bin_type=True))

    dst_path.write_bytes(bytes(out))


def _runtime_base_dir() -> Path:
    # PyInstaller one-file/one-dir builds expose sys.frozen + sys.executable.
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _resolve_csv_path(raw_path: str | None) -> Path:
    if raw_path is None or not raw_path.strip():
        return _runtime_base_dir() / DEFAULT_NAME_CSV
    path = Path(raw_path.strip('"'))
    if path.is_absolute():
        return path
    return _runtime_base_dir() / path


def _read_code_segments(src_path: Path) -> list[dict[str, Any]]:
    segments_raw = list(
        msgpack.Unpacker(
            src_path.open("rb"),
            raw=False,
            strict_map_key=False,
            timestamp=0,
        )
    )
    if not segments_raw:
        raise ValueError("Input .code has no MessagePack segments")

    segments: list[dict[str, Any]] = []
    for idx, segment_raw in enumerate(segments_raw):
        ext_code, block_sizes, payload = _decode_lz4_block_array(segment_raw)
        segments.append(
            {
                "index": idx,
                "ext_code": ext_code,
                "block_sizes": block_sizes,
                "payload": payload,
            }
        )
    return segments


def _write_code_segments(dst_path: Path, segments: list[dict[str, Any]]) -> None:
    out = bytearray()
    for idx, segment in enumerate(segments):
        ext_code = int(segment.get("ext_code", LZ4_BLOCK_ARRAY_EXT_CODE))
        block_sizes = segment.get("block_sizes")
        payload = segment.get("payload")
        if not isinstance(payload, (bytes, bytearray)):
            raise TypeError(f"Segment {idx} payload must be bytes")
        segment_raw = _encode_lz4_block_array(bytes(payload), ext_code, block_sizes)
        out.extend(msgpack.packb(segment_raw, use_bin_type=True))
    dst_path.write_bytes(bytes(out))


def _load_script_from_code(src_path: Path) -> tuple[list[dict[str, Any]], int, dict[str, Any]]:
    if src_path.suffix.lower() != ".code":
        raise ValueError(f"Name operations only support .code files: {src_path}")

    segments = _read_code_segments(src_path)
    if len(segments) < 2:
        raise ValueError(f".code has fewer than 2 segments: {src_path}")

    script_idx = 1
    payload = segments[script_idx].get("payload")
    if not isinstance(payload, (bytes, bytearray)):
        raise TypeError("Script segment payload is not bytes")

    script_obj = _parse_script_payload(bytes(payload))
    if not isinstance(script_obj, dict):
        raise TypeError("Parsed script payload is not an object")
    return segments, script_idx, script_obj


def _iter_script_value_lists(script_obj: dict[str, Any]) -> Iterable[list[Any]]:
    type_infos = script_obj.get("typeInfos")
    if not isinstance(type_infos, (dict, MapNode)):
        return

    for _, type_info in _iter_map_entries(type_infos):
        if not isinstance(type_info, (dict, MapNode)):
            continue
        methods: Any = None
        for key, val in _iter_map_entries(type_info):
            if key == "methods":
                methods = val
                break
        if not isinstance(methods, (dict, MapNode)):
            continue
        for _, method in _iter_map_entries(methods):
            if not isinstance(method, (dict, MapNode)):
                continue
            values: Any = None
            for key, val in _iter_map_entries(method):
                if key == "values":
                    values = val
                    break
            if isinstance(values, list):
                yield values


def _iter_name_matches(
    script_obj: dict[str, Any],
) -> Iterable[tuple[int, list[Any], int, str]]:
    match_idx = 0
    for values in _iter_script_value_lists(script_obj):
        for i in range(0, len(values) - 2):
            if (
                values[i] == "input"
                and values[i + 2] == "__Check"
                and isinstance(values[i + 1], str)
            ):
                yield (match_idx, values, i + 1, values[i + 1])
                match_idx += 1


class _RawMapPairs(list):
    """Preserve raw MessagePack map entry order for stream-level edits."""


def _raw_iter_stream_with_slices(payload: bytes) -> Iterable[tuple[Any, int, int]]:
    unpacker = msgpack.Unpacker(
        raw=False,
        strict_map_key=False,
        timestamp=0,
        use_list=False,
        object_pairs_hook=_RawMapPairs,
    )
    unpacker.feed(payload)
    start = 0
    for obj in unpacker:
        end = unpacker.tell()
        yield obj, start, end
        start = end


def _raw_pack_obj(packer: msgpack.Packer, obj: Any) -> bytes:
    if isinstance(obj, msgpack.ExtType):
        return packer.pack(obj)

    if isinstance(obj, msgpack.Timestamp):
        return packer.pack(obj)

    if isinstance(obj, _RawMapPairs):
        parts = [packer.pack_map_header(len(obj))]
        for pair in obj:
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                raise ValueError(f"Invalid raw map pair entry: {pair!r}")
            parts.append(_raw_pack_obj(packer, pair[0]))
            parts.append(_raw_pack_obj(packer, pair[1]))
        return b"".join(parts)

    if isinstance(obj, dict):
        parts = [packer.pack_map_header(len(obj))]
        for key, val in obj.items():
            parts.append(_raw_pack_obj(packer, key))
            parts.append(_raw_pack_obj(packer, val))
        return b"".join(parts)

    if isinstance(obj, (list, tuple)):
        parts = [packer.pack_array_header(len(obj))]
        for item in obj:
            parts.append(_raw_pack_obj(packer, item))
        return b"".join(parts)

    return packer.pack(obj)


def _parse_msgpack_array_header(data: bytes) -> tuple[int, int]:
    """Parse a msgpack array header. Returns (header_byte_size, element_count)."""
    b0 = data[0]
    if 0x90 <= b0 <= 0x9F:
        return 1, b0 & 0x0F
    if b0 == 0xDC:
        return 3, struct.unpack(">H", data[1:3])[0]
    if b0 == 0xDD:
        return 5, struct.unpack(">I", data[1:5])[0]
    raise ValueError(f"Not a msgpack array header: 0x{b0:02X}")


def _build_msgpack_array_header(count: int) -> bytes:
    """Build a msgpack array header for the given element count."""
    if count <= 15:
        return bytes([0x90 | count])
    if count <= 65535:
        return struct.pack(">BH", 0xDC, count)
    return struct.pack(">BI", 0xDD, count)


def _walk_msgpack_array_element_bounds(
    data: bytes, header_size: int, count: int
) -> list[tuple[int, int]]:
    """Walk through array elements and return byte boundaries.

    Returns list of (start_offset, end_offset) where offsets are relative to
    data[0] (i.e. they include the header offset).
    """
    unpacker = msgpack.Unpacker(raw=False, strict_map_key=False, timestamp=0)
    unpacker.feed(data[header_size:])
    bounds: list[tuple[int, int]] = []
    for _ in range(count):
        start = header_size + unpacker.tell()
        unpacker.unpack()
        end = header_size + unpacker.tell()
        bounds.append((start, end))
    return bounds


def _splice_keyboard_block_bytes(
    block_bytes: bytes,
    modifications: list[tuple[int, str | None, list[str]]],
    kb_length_elem_idx: int | None,
    new_kb_length: int | None,
) -> bytes:
    """Byte-level splice of a keyboard block array.

    Preserves original byte encoding for all unmodified elements.

    modifications: list of (answer_elem_idx, primary_text_or_None, alias_texts).
        Must be sorted by answer_elem_idx ascending.
    kb_length_elem_idx: element index of the keyboard length value, or None.
    new_kb_length: new length value, or None to keep original.
    """
    header_size, count = _parse_msgpack_array_header(block_bytes)
    bounds = _walk_msgpack_array_element_bounds(block_bytes, header_size, count)

    mod_map: dict[int, tuple[str | None, list[str]]] = {}
    for idx, primary, aliases in modifications:
        mod_map[idx] = (primary, aliases)

    packer = msgpack.Packer(use_bin_type=True)
    parts: list[bytes] = []
    extra_count = 0

    for i in range(count):
        elem_start, elem_end = bounds[i]
        original_elem = block_bytes[elem_start:elem_end]

        if i in mod_map:
            primary, aliases = mod_map[i]
            if primary is not None:
                parts.append(packer.pack(primary))
            else:
                parts.append(original_elem)
            for alias in aliases:
                if alias == primary:
                    continue
                parts.append(packer.pack("result"))
                parts.append(packer.pack("input"))
                parts.append(packer.pack(alias))
                extra_count += 3
        elif i == kb_length_elem_idx and new_kb_length is not None:
            parts.append(_pack_int32(new_kb_length))
        else:
            parts.append(original_elem)

    new_header = _build_msgpack_array_header(count + extra_count)
    return new_header + b"".join(parts)


def _patch_codes_indices(
    payload: bytes,
    codes_end_pos: int,
    codes_count: int,
    shifts: list[tuple[int, int]],
) -> bytes:
    """Patch code index fields in the payload to account for value insertions.

    shifts: list of (insertion_after_idx, element_count_inserted), sorted ascending.
    codes_end_pos: byte offset in payload where codes data ends.
    Each code entry is 12 bytes: cc XX d1 XX XX d2 XX XX XX XX d0 XX
    The int16 index field is at bytes 2..4 (the d1 XX XX part).
    """
    if not shifts or codes_count == 0:
        return payload

    codes_data_start = codes_end_pos - codes_count * 12
    buf = bytearray(payload)

    for entry_idx in range(codes_count):
        off = codes_data_start + entry_idx * 12
        idx_val = struct.unpack(">h", buf[off + 3 : off + 5])[0]
        new_idx = idx_val
        for after_pos, shift_amount in shifts:
            if idx_val > after_pos:
                new_idx += shift_amount
        if new_idx != idx_val:
            if not -32768 <= new_idx <= 32767:
                raise ValueError(
                    f"Shifted code index out of int16 range: {idx_val} -> {new_idx}"
                )
            struct.pack_into(">h", buf, off + 3, new_idx)

    return bytes(buf)


def _raw_has_keyboard_signature(node: Any) -> bool:
    if isinstance(node, (list, tuple)):
        seq = list(node)
        if (
            "KeyboardType" in seq
            and "Input" in seq
            and ("Keyboard" in seq or "SoftwareKeyboard" in seq)
        ):
            return True
        return any(_raw_has_keyboard_signature(item) for item in seq)

    if isinstance(node, _RawMapPairs):
        for pair in node:
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                continue
            if _raw_has_keyboard_signature(pair[0]) or _raw_has_keyboard_signature(pair[1]):
                return True
        return False

    if isinstance(node, dict):
        for key, val in node.items():
            if _raw_has_keyboard_signature(key) or _raw_has_keyboard_signature(val):
                return True
        return False

    return False


def _raw_to_mutable(node: Any) -> Any:
    if isinstance(node, _RawMapPairs):
        out = _RawMapPairs()
        for pair in node:
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                out.append(pair)
                continue
            out.append([_raw_to_mutable(pair[0]), _raw_to_mutable(pair[1])])
        return out

    if isinstance(node, dict):
        return {_raw_to_mutable(key): _raw_to_mutable(val) for key, val in node.items()}

    if isinstance(node, (list, tuple)):
        return [_raw_to_mutable(item) for item in node]

    return node


def _iter_raw_keyboard_matches(node: Any) -> Iterable[tuple[list[Any], int, int, str]]:
    if isinstance(node, list):
        if (
            "KeyboardType" in node
            and "Input" in node
            and ("Keyboard" in node or "SoftwareKeyboard" in node)
        ):
            for i in range(0, len(node) - 2):
                if (
                    node[i] == "input"
                    and isinstance(node[i + 1], str)
                    and node[i + 2] == "__Check"
                ):
                    yield (node, i, i + 1, node[i + 1])
        for item in node:
            yield from _iter_raw_keyboard_matches(item)
        return

    if isinstance(node, tuple):
        seq = list(node)
        yield from _iter_raw_keyboard_matches(seq)
        return

    if isinstance(node, _RawMapPairs):
        for pair in node:
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                continue
            yield from _iter_raw_keyboard_matches(pair[0])
            yield from _iter_raw_keyboard_matches(pair[1])
        return

    if isinstance(node, dict):
        for key, val in node.items():
            yield from _iter_raw_keyboard_matches(key)
            yield from _iter_raw_keyboard_matches(val)


def _split_name_variants(dst_text: str) -> list[str]:
    normalized = dst_text.replace("\r\n", "\n").replace("\r", "\n")
    seen: set[str] = set()
    variants: list[str] = []
    for line in normalized.split("\n"):
        for part in line.split("||"):
            text = part.strip()
            if not text or text in seen:
                continue
            seen.add(text)
            variants.append(text)
    return variants


def _build_name_replacements(
    csv_rows: list[dict[str, Any]],
) -> dict[int, list[tuple[str, str, int]]]:
    replacements: dict[int, list[tuple[str, str, int]]] = {}
    for row in csv_rows:
        idx_text = str(row.get("idx", "")).strip()
        if not idx_text:
            continue
        try:
            idx_value = int(idx_text)
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"Invalid idx at CSV line {row.get('line_no')}: {idx_text}") from exc
        if idx_value < 0:
            raise ValueError(f"idx must be >= 0 at CSV line {row.get('line_no')}: {idx_value}")

        src_text = str(row.get("src", ""))
        line_no = int(row.get("line_no", 0))
        for variant in _split_name_variants(str(row.get("dst", ""))):
            replacements.setdefault(idx_value, []).append((src_text, variant, line_no))

    return replacements


def _raw_answer_chunk_bounds(seq: list[Any], input_pos: int) -> tuple[int, int]:
    start = input_pos - 1 if input_pos > 0 and seq[input_pos - 1] == "result" else input_pos
    next_input = len(seq)
    for pos in range(input_pos + 1, len(seq) - 2):
        if (
            seq[pos] == "input"
            and isinstance(seq[pos + 1], str)
            and seq[pos + 2] == "__Check"
        ):
            next_input = pos
            break

    for pos in range(input_pos + 3, next_input):
        if seq[pos] == "__EndLabel":
            return start, pos

    return start, next_input - 1


def _find_raw_keyboard_length_pos(seq: list[Any]) -> int | None:
    for pos in range(0, len(seq) - 4):
        if seq[pos] != "KeyboardType":
            continue
        if not isinstance(seq[pos + 1], str):
            continue
        if not isinstance(seq[pos + 2], int):
            continue
        if seq[pos + 3] not in {"Keyboard", "SoftwareKeyboard"}:
            continue
        if seq[pos + 4] != "Input":
            continue
        return pos + 2
    return None


def _apply_variants_to_raw_match(
    seq: list[Any],
    input_pos: int,
    primary_text: str,
    alias_texts: list[str],
) -> int:
    answer_pos = input_pos + 1
    changed = 0

    if seq[answer_pos] != primary_text:
        seq[answer_pos] = primary_text
        changed += 1

    if not alias_texts:
        return changed

    insert_pos = answer_pos + 1

    for alias in alias_texts:
        if alias == primary_text:
            continue
        alias_chunk = ["result", "input", alias]
        seq[insert_pos:insert_pos] = alias_chunk
        insert_pos += len(alias_chunk)
        changed += 1

    return changed


def _extract_name_rows_from_code_structured(src_path: Path) -> list[dict[str, str]]:
    _, _, script_obj = _load_script_from_code(src_path)
    rows: list[dict[str, str]] = []
    for idx, _, _, src_text in _iter_name_matches(script_obj):
        rows.append(
            {
                "file": src_path.name,
                "idx": str(idx),
                "src": src_text,
                "dst": "",
            }
        )
    return rows


def _extract_name_rows_from_code_raw(src_path: Path) -> list[dict[str, str]]:
    segments = _read_code_segments(src_path)
    if len(segments) < 2:
        raise ValueError(f".code has fewer than 2 segments: {src_path}")

    payload = segments[1].get("payload")
    if not isinstance(payload, (bytes, bytearray)):
        raise TypeError("Script segment payload is not bytes")

    rows: list[dict[str, str]] = []
    match_idx = 0
    for obj, _, _ in _raw_iter_stream_with_slices(bytes(payload)):
        if not _raw_has_keyboard_signature(obj):
            continue
        for _, _, _, src_text in _iter_raw_keyboard_matches(obj):
            rows.append(
                {
                    "file": src_path.name,
                    "idx": str(match_idx),
                    "src": src_text,
                    "dst": "",
                }
            )
            match_idx += 1

    if not rows:
        raise ValueError(f"No keyboard answer blocks found: {src_path}")

    return rows


def _extract_name_rows_from_code(src_path: Path) -> list[dict[str, str]]:
    raw_exc: Exception | None = None
    try:
        return _extract_name_rows_from_code_raw(src_path)
    except Exception as exc:  # noqa: BLE001
        raw_exc = exc

    try:
        return _extract_name_rows_from_code_structured(src_path)
    except Exception:
        if raw_exc is not None:
            raise raw_exc
        raise


def _write_name_csv(rows: list[dict[str, str]], csv_path: Path) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8-sig", newline="") as fp:
        writer = csv.DictWriter(
            fp,
            fieldnames=["file", "idx", "src", "dst"],
            quoting=csv.QUOTE_ALL,
            lineterminator="\n",
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "file": str(row.get("file", "")),
                    "idx": str(row.get("idx", "")),
                    "src": str(row.get("src", "")),
                    "dst": str(row.get("dst", "")),
                }
            )


def _read_name_csv(csv_path: Path) -> list[dict[str, Any]]:
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV not found: {csv_path}")

    rows: list[dict[str, Any]] = []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as fp:
        reader = csv.DictReader(fp)
        required = {"file", "idx", "src", "dst"}
        if reader.fieldnames is None:
            raise ValueError("CSV has no header")
        missing = [name for name in required if name not in reader.fieldnames]
        if missing:
            raise ValueError(f"CSV missing required columns: {', '.join(sorted(missing))}")

        for line_no, row in enumerate(reader, start=2):
            rows.append(
                {
                    "line_no": line_no,
                    "file": str(row.get("file", "") or ""),
                    "idx": str(row.get("idx", "") or ""),
                    "src": str(row.get("src", "") or ""),
                    "dst": str(row.get("dst", "") or ""),
                }
            )
    return rows


def _normalize_csv_row_file(raw_file: str) -> str:
    text = raw_file.strip().strip('"').replace("\\", "/")
    return Path(text).name.lower()


def _find_method_for_values(
    script_obj: dict[str, Any], target_values: list[Any]
) -> dict[str, Any] | None:
    """Find the method object that owns a specific values list."""
    type_infos = script_obj.get("typeInfos")
    if not isinstance(type_infos, (dict, MapNode)):
        return None
    for _, type_info in _iter_map_entries(type_infos):
        if not isinstance(type_info, (dict, MapNode)):
            continue
        methods: Any = None
        for key, val in _iter_map_entries(type_info):
            if key == "methods":
                methods = val
                break
        if not isinstance(methods, (dict, MapNode)):
            continue
        for _, method in _iter_map_entries(methods):
            if not isinstance(method, (dict, MapNode)):
                continue
            for key, val in _iter_map_entries(method):
                if key == "values" and val is target_values:
                    return method
    return None


def _insert_alias_code_entries(
    method_obj: dict[str, Any] | MapNode,
    answer_val_idx: int,
    alias_val_indices: list[tuple[int, int, int]],
) -> int:
    """Insert code entries for alias answers into a method's codes blob.

    answer_val_idx: the values index of the primary answer text.
    alias_val_indices: list of (result_idx, input_idx, answer_idx) for each alias.

    Returns the number of code entries inserted.
    """
    codes_b64: str | None = None
    codes_count: int = 0
    for key, val in _iter_map_entries(method_obj):
        if key == "codes_b64":
            codes_b64 = val
        elif key == "codes_count":
            codes_count = val

    if not codes_b64 or codes_count == 0:
        return 0

    raw = base64.b64decode(codes_b64)
    # Parse array header
    hdr_size, count = _parse_msgpack_array_header(raw)
    data = bytearray(raw[hdr_size:])

    # Find the compare-jump (op=25 idx=-1 count=11) that follows the primary answer
    # The primary answer is referenced by op=7 idx=answer_val_idx
    insert_after_entry = -1
    for ci in range(count):
        off = ci * 12
        opcode = data[off + 1]
        idx = struct.unpack(">h", data[off + 3 : off + 5])[0]
        cnt = struct.unpack(">i", data[off + 6 : off + 10])[0]
        if opcode == 7 and idx == answer_val_idx:
            # Found the primary answer load; the next op=25 count=11 is the compare jump
            for cj in range(ci + 1, min(ci + 3, count)):
                off2 = cj * 12
                if data[off2 + 1] == 25 and struct.unpack(">i", data[off2 + 6 : off2 + 10])[0] == 11:
                    insert_after_entry = cj
                    break
            break

    if insert_after_entry < 0:
        return 0

    # Build new code entries for each alias
    new_entries = bytearray()
    for result_idx, input_idx, answer_idx in alias_val_indices:
        new_entries.extend(_pack_uint8(3))       # op=3 (result)
        new_entries.extend(_pack_int16(result_idx))
        new_entries.extend(_pack_int32(0))
        new_entries.extend(_pack_int8(-1))

        new_entries.extend(_pack_uint8(10))      # op=10 (input)
        new_entries.extend(_pack_int16(input_idx))
        new_entries.extend(_pack_int32(-1))
        new_entries.extend(_pack_int8(-1))

        new_entries.extend(_pack_uint8(7))       # op=7 (load value)
        new_entries.extend(_pack_int16(answer_idx))
        new_entries.extend(_pack_int32(-1))
        new_entries.extend(_pack_int8(-1))

        new_entries.extend(_pack_uint8(25))      # op=25 (compare jump)
        new_entries.extend(_pack_int16(-1))
        new_entries.extend(_pack_int32(11))
        new_entries.extend(_pack_int8(-1))

    num_new = len(alias_val_indices) * 4

    # Insert after the compare jump entry
    insert_byte = (insert_after_entry + 1) * 12
    new_data = bytes(data[:insert_byte]) + bytes(new_entries) + bytes(data[insert_byte:])

    # Build new codes blob with updated count
    new_count = count + num_new
    new_header = _build_msgpack_array_header(new_count)
    new_blob = new_header + new_data

    # Update method's codes_b64 and codes_count
    new_b64 = base64.b64encode(new_blob).decode("ascii")
    if isinstance(method_obj, MapNode):
        for entry in method_obj:
            if isinstance(entry, list) and len(entry) == 2:
                if entry[0] == "codes_b64":
                    entry[1] = new_b64
                elif entry[0] == "codes_count":
                    entry[1] = new_count
    elif isinstance(method_obj, dict):
        method_obj["codes_b64"] = new_b64
        method_obj["codes_count"] = new_count

    return num_new


def _shift_codes_indices(
    method_obj: dict[str, Any] | MapNode,
    after_idx: int,
    shift: int,
) -> None:
    """Shift all code index references > after_idx by shift amount."""
    codes_b64: str | None = None
    codes_count: int = 0
    for key, val in _iter_map_entries(method_obj):
        if key == "codes_b64":
            codes_b64 = val
        elif key == "codes_count":
            codes_count = val

    if not codes_b64 or codes_count == 0:
        return

    raw = base64.b64decode(codes_b64)
    hdr_size, count = _parse_msgpack_array_header(raw)
    data = bytearray(raw[hdr_size:])

    modified = False
    for ci in range(count):
        off = ci * 12
        idx = struct.unpack(">h", data[off + 3 : off + 5])[0]
        if idx > after_idx:
            new_idx = idx + shift
            struct.pack_into(">h", data, off + 3, new_idx)
            modified = True

    if modified:
        new_blob = raw[:hdr_size] + bytes(data)
        new_b64 = base64.b64encode(new_blob).decode("ascii")
        if isinstance(method_obj, MapNode):
            for entry in method_obj:
                if isinstance(entry, list) and len(entry) == 2 and entry[0] == "codes_b64":
                    entry[1] = new_b64
        elif isinstance(method_obj, dict):
            method_obj["codes_b64"] = new_b64


def _apply_name_rows_to_code_structured(
    src_path: Path, csv_rows: list[dict[str, Any]]
) -> tuple[int, int, int]:
    replacements = _build_name_replacements(csv_rows)
    if not replacements:
        return (0, 0, 0)

    segments, script_idx, script_obj = _load_script_from_code(src_path)

    hit_indices: set[int] = set()
    changed = 0
    matches = list(_iter_name_matches(script_obj))
    required_lengths: dict[int, int] = {}
    values_by_id: dict[int, list[Any]] = {}

    for match_idx, values, _, _ in matches:
        if match_idx not in replacements:
            continue
        replacement_group = replacements[match_idx]
        values_by_id[id(values)] = values
        required_lengths[id(values)] = max(
            required_lengths.get(id(values), 0),
            max(len(text) for _, text, _ in replacement_group),
        )

    # Process in reverse to keep earlier indices stable
    for match_idx, values, name_pos, current_src in reversed(matches):
        if match_idx not in replacements:
            continue
        replacement_group = replacements[match_idx]
        expected_src, primary_text, line_no = replacement_group[0]
        hit_indices.add(match_idx)

        if expected_src and current_src != expected_src and current_src != primary_text:
            print(
                f"[WARN] {src_path.name} idx={match_idx} (CSV line {line_no}) "
                f"src mismatch: csv='{expected_src}' file='{current_src}'"
            )

        alias_texts: list[str] = []
        seen_aliases = {primary_text}
        for _, alias_text, _ in replacement_group[1:]:
            if alias_text in seen_aliases:
                continue
            seen_aliases.add(alias_text)
            alias_texts.append(alias_text)

        if not alias_texts and current_src == primary_text:
            continue

        answer_pos = name_pos  # index of the answer text in values
        input_pos = name_pos - 1  # index of 'input' keyword

        # Replace primary text if needed
        if current_src != primary_text:
            values[answer_pos] = primary_text
            changed += 1

        if alias_texts:
            # Insert alias values: "result", "input", alias for each
            insert_pos = answer_pos + 1
            num_inserted = 0
            alias_val_indices: list[tuple[int, int, int]] = []
            for alias in alias_texts:
                result_idx = insert_pos + num_inserted
                input_idx = result_idx + 1
                answer_idx = input_idx + 1
                values[insert_pos + num_inserted : insert_pos + num_inserted] = [
                    "result", "input", alias
                ]
                alias_val_indices.append((result_idx, input_idx, answer_idx))
                num_inserted += 3

            # Find and update the method's codes
            method_obj = _find_method_for_values(script_obj, values)
            if method_obj is not None:
                # First shift existing codes indices
                _shift_codes_indices(method_obj, answer_pos, num_inserted)
                # Then insert new code entries for aliases
                _insert_alias_code_entries(method_obj, answer_pos, alias_val_indices)

            changed += len(alias_texts)

    for values_id, required_length in required_lengths.items():
        values = values_by_id[values_id]
        length_pos = _find_raw_keyboard_length_pos(values)
        if length_pos is None:
            continue
        current_length = values[length_pos]
        if not isinstance(current_length, int) or current_length >= required_length:
            continue
        values[length_pos] = required_length
        changed += 1

    missing = sorted(set(replacements.keys()) - hit_indices)
    for missing_idx in missing:
        _, _, line_no = replacements[missing_idx][0]
        print(
            f"[WARN] {src_path.name} idx={missing_idx} (CSV line {line_no}) "
            "not found in file"
        )

    if changed > 0:
        segments[script_idx]["payload"] = _encode_script_payload(script_obj)
        _write_code_segments(src_path, segments)

    return (len(replacements), len(hit_indices), changed)


def _apply_name_rows_to_code_raw(src_path: Path, csv_rows: list[dict[str, Any]]) -> tuple[int, int, int]:
    replacements = _build_name_replacements(csv_rows)
    if not replacements:
        return (0, 0, 0)

    segments = _read_code_segments(src_path)
    if len(segments) < 2:
        raise ValueError(f".code has fewer than 2 segments: {src_path}")

    payload = segments[1].get("payload")
    if not isinstance(payload, (bytes, bytearray)):
        raise TypeError("Script segment payload is not bytes")

    rebuilt: list[bytes] = []
    hit_indices: set[int] = set()
    changed = 0
    match_idx = 0
    found_keyboard = False
    code_index_shifts: dict[int, list[tuple[int, int]]] = {}
    last_values_key_pos: int | None = None

    for obj, start, end in _raw_iter_stream_with_slices(bytes(payload)):
        # Track "values" key positions for codes patching
        if isinstance(obj, str) and obj == "values":
            last_values_key_pos = start
        raw_obj = bytes(payload[start:end])
        if not _raw_has_keyboard_signature(obj):
            rebuilt.append(raw_obj)
            continue

        found_keyboard = True
        mutable_obj = _raw_to_mutable(obj)
        matches = list(_iter_raw_keyboard_matches(mutable_obj))
        if not matches:
            rebuilt.append(raw_obj)
            continue

        base_idx = match_idx
        match_idx += len(matches)

        # Collect modifications: (answer_elem_idx, primary_text, alias_list)
        splice_mods: list[tuple[int, str | None, list[str]]] = []
        required_length = 0
        any_hit = False

        for local_idx, match in enumerate(matches):
            global_idx = base_idx + local_idx
            if global_idx not in replacements:
                continue

            seq, input_pos, answer_pos, current_src = match
            replacement_group = replacements[global_idx]
            expected_src, primary_text, line_no = replacement_group[0]
            hit_indices.add(global_idx)
            any_hit = True

            if expected_src and current_src != expected_src and current_src != primary_text:
                print(
                    f"[WARN] {src_path.name} idx={global_idx} (CSV line {line_no}) "
                    f"src mismatch: csv='{expected_src}' file='{current_src}'"
                )

            alias_texts: list[str] = []
            seen_aliases = {primary_text}
            for _, alias_text, _ in replacement_group[1:]:
                if alias_text in seen_aliases:
                    continue
                seen_aliases.add(alias_text)
                alias_texts.append(alias_text)

            needs_replace = current_src != primary_text
            primary_for_splice = primary_text if needs_replace else None

            if primary_for_splice is not None or alias_texts:
                splice_mods.append((answer_pos, primary_for_splice, alias_texts))
                changed += (1 if needs_replace else 0) + len(alias_texts)

            for text in [primary_text] + alias_texts:
                required_length = max(required_length, len(text))

        if not any_hit:
            rebuilt.append(raw_obj)
            continue

        # Determine keyboard length adjustment
        kb_length_idx = _find_raw_keyboard_length_pos(list(mutable_obj) if not isinstance(mutable_obj, list) else mutable_obj)
        new_kb_len: int | None = None
        if kb_length_idx is not None and required_length > 0:
            current_kb_len = mutable_obj[kb_length_idx] if isinstance(mutable_obj, list) else None
            if isinstance(current_kb_len, int) and current_kb_len < required_length:
                new_kb_len = required_length
                changed += 1

        if splice_mods or new_kb_len is not None:
            rebuilt.append(
                _splice_keyboard_block_bytes(
                    raw_obj, splice_mods, kb_length_idx, new_kb_len
                )
            )
            # Record index shifts for codes patching
            if splice_mods and last_values_key_pos is not None:
                shifts: list[tuple[int, int]] = []
                for ans_idx, primary, aliases in splice_mods:
                    inserted = sum(3 for a in aliases if a != primary)
                    if inserted > 0:
                        shifts.append((ans_idx, inserted))
                if shifts:
                    code_index_shifts[last_values_key_pos] = shifts
        else:
            rebuilt.append(raw_obj)

    missing = sorted(set(replacements.keys()) - hit_indices)
    for missing_idx in missing:
        _, _, line_no = replacements[missing_idx][0]
        print(
            f"[WARN] {src_path.name} idx={missing_idx} (CSV line {line_no}) "
            "not found in file"
        )

    if not found_keyboard:
        raise ValueError(f"No keyboard answer blocks found: {src_path}")

    if changed > 0:
        new_payload = b"".join(rebuilt)

        # Patch codes indices for each method whose values were modified.
        # In the payload stream, codes precede values.  We locate the codes
        # by scanning backwards from each modified keyboard block.
        if code_index_shifts:
            for values_key_pos, shifts in code_index_shifts.items():
                # Find the codes for the method that owns this values block.
                # The method map in the stream has: ... "codes" ARRAY ... "values" ARRAY ...
                # Scan backwards from the "values" key position to find the codes array.
                codes_count, codes_end = _find_codes_before_pos(
                    bytes(payload), values_key_pos
                )
                if codes_count > 0 and codes_end > 0:
                    new_payload = _patch_codes_indices(
                        new_payload, codes_end, codes_count, shifts
                    )

        segments[1]["payload"] = new_payload
        _write_code_segments(src_path, segments)

    return (len(replacements), len(hit_indices), changed)


def _find_codes_before_pos(payload: bytes, values_key_pos: int) -> tuple[int, int]:
    """Find the codes array that belongs to the same method as a 'values' key.

    Scans backwards from values_key_pos looking for the 'codes' key.
    Returns (codes_entry_count, codes_data_end_byte_offset).
    """
    # The method map has entries like: "codes" <codes_array> "values" <values_array> ...
    # Between "codes" key and its data, there may be other keys.
    # We look for the pattern: "codes" key followed by an array of code entries.
    search_start = max(0, values_key_pos - 200000)
    codes_key_encoded = b"\xa5codes"  # fixstr(5) "codes"

    pos = payload.rfind(codes_key_encoded, search_start, values_key_pos)
    if pos < 0:
        return 0, 0

    # Right after the "codes" key, there should be an array header
    arr_start = pos + len(codes_key_encoded)
    if arr_start >= len(payload):
        return 0, 0

    try:
        header_size, count = _parse_msgpack_array_header(payload[arr_start:])
    except (ValueError, IndexError):
        return 0, 0

    codes_data_end = arr_start + header_size + count * 12
    return count, codes_data_end


def _apply_name_rows_to_code(src_path: Path, csv_rows: list[dict[str, Any]]) -> tuple[int, int, int]:
    structured_exc: Exception | None = None
    try:
        return _apply_name_rows_to_code_structured(src_path, csv_rows)
    except Exception as exc:  # noqa: BLE001
        structured_exc = exc

    try:
        return _apply_name_rows_to_code_raw(src_path, csv_rows)
    except Exception:
        if structured_exc is not None:
            raise structured_exc
        raise


def _name_export_single(src_path: Path, csv_path: Path) -> bool:
    if not src_path.exists():
        print(f"[FAIL] not found: {src_path}")
        return False
    if src_path.suffix.lower() != ".code":
        print(f"[FAIL] name export supports only .code: {src_path}")
        return False
    try:
        rows = _extract_name_rows_from_code(src_path)
        _write_name_csv(rows, csv_path)
        print(f"[OK] exported {len(rows)} rows -> {csv_path}")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] {src_path}: {exc}")
        return False


def _name_export_dir(dir_path: Path, csv_path: Path) -> bool:
    files = sorted(
        p for p in dir_path.iterdir() if p.is_file() and p.suffix.lower() == ".code"
    )
    if not files:
        print(f"[SKIP] no .code files found in: {dir_path}")
        _write_name_csv([], csv_path)
        print(f"[OK] exported 0 rows -> {csv_path}")
        return True

    all_rows: list[dict[str, str]] = []
    failures = 0
    for src in files:
        try:
            rows = _extract_name_rows_from_code(src)
            all_rows.extend(rows)
            print(f"[OK] scan {src.name}: {len(rows)} rows")
        except Exception as exc:  # noqa: BLE001
            print(f"[FAIL] {src}: {exc}")
            failures += 1

    _write_name_csv(all_rows, csv_path)
    print(f"[OK] exported {len(all_rows)} rows from {len(files)} files -> {csv_path}")
    return failures == 0


def _name_import_single(src_path: Path, csv_path: Path) -> bool:
    if not src_path.exists():
        print(f"[FAIL] not found: {src_path}")
        return False
    if src_path.suffix.lower() != ".code":
        print(f"[FAIL] name import supports only .code: {src_path}")
        return False
    try:
        csv_rows = _read_name_csv(csv_path)
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] {exc}")
        return False

    matched_rows = [
        row
        for row in csv_rows
        if _normalize_csv_row_file(str(row.get("file", ""))) == src_path.name.lower()
    ]
    if not matched_rows:
        print(f"[SKIP] no CSV rows for file: {src_path.name}")
        return True

    try:
        selected, hit, changed = _apply_name_rows_to_code(src_path, matched_rows)
        print(
            f"[OK] imported {src_path.name}: selected={selected}, "
            f"matched={hit}, changed={changed}"
        )
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] {src_path}: {exc}")
        return False


def _name_import_dir(dir_path: Path, csv_path: Path) -> bool:
    try:
        csv_rows = _read_name_csv(csv_path)
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] {exc}")
        return False

    rows_by_file: dict[str, list[dict[str, Any]]] = {}
    for row in csv_rows:
        file_name = _normalize_csv_row_file(str(row.get("file", "")))
        if not file_name:
            line_no = row.get("line_no")
            print(f"[WARN] CSV line {line_no}: empty file column, skipped")
            continue
        rows_by_file.setdefault(file_name, []).append(row)

    if not rows_by_file:
        print(f"[SKIP] no import rows in CSV: {csv_path}")
        return True

    failures = 0
    for file_name, rows in sorted(rows_by_file.items()):
        target = dir_path / file_name
        if not target.exists() or not target.is_file():
            print(f"[FAIL] target file not found: {target}")
            failures += 1
            continue
        if target.suffix.lower() != ".code":
            print(f"[SKIP] target is not .code: {target.name}")
            continue

        try:
            selected, hit, changed = _apply_name_rows_to_code(target, rows)
            print(
                f"[OK] imported {target.name}: selected={selected}, "
                f"matched={hit}, changed={changed}"
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[FAIL] {target}: {exc}")
            failures += 1

    return failures == 0


def _print_usage() -> None:
    print("Usage:")
    print("  Drag and drop one or more .code/.json files onto this script")
    print("  or run: python code_json_converter.py [--include-raw] <file1> [file2 ...]")
    print("  or run: python code_json_converter.py --dir <folder> --code-to-json [--include-raw]")
    print("  or run: python code_json_converter.py --dir <folder> --json-to-code")
    print("  or run: python code_json_converter.py <file.code> --name-export [name.csv]")
    print("  or run: python code_json_converter.py <file.code> --name-import [name.csv]")
    print("  or run: python code_json_converter.py --dir <folder> --name-export [name.csv]")
    print("  or run: python code_json_converter.py --dir <folder> --name-import [name.csv]")
    print("")
    print("Options:")
    print("  --include-raw   Keep payload_b64 in JSON for fallback/lossless backup")
    print("  --dir <folder>  Convert all files in folder (non-recursive)")
    print("  --code-to-json  With --dir: convert all *.code to *.json")
    print("  --json-to-code  With --dir: convert all *.json to *.code")
    print("  --name-export   Export input/<name>/__Check names to CSV")
    print("  --name-import   Import translated names from CSV dst column")
    print("                  Use dst='answer1||answer2' to add extra accepted aliases")
    print("  --name-csv <p>  CSV path (default: runtime_dir/AITSF2_CodeName.csv)")


def _convert_single_file(src: Path, include_raw: bool) -> bool:
    if not src.exists():
        print(f"[FAIL] not found: {src}")
        return False

    suffix = src.suffix.lower()
    try:
        if suffix == ".code":
            dst = src.with_suffix(".json")
            code_to_json(src, dst, include_raw=include_raw)
            print(f"[OK] {src.name} -> {dst.name}")
            return True
        if suffix == ".json":
            dst = src.with_suffix(".code")
            json_to_code(src, dst)
            print(f"[OK] {src.name} -> {dst.name}")
            return True

        print(f"[SKIP] unsupported extension: {src}")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] {src}: {exc}")
        return False


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        _print_usage()
        return 1

    include_raw = False
    dir_mode: Path | None = None
    mode_code_to_json = False
    mode_json_to_code = False
    name_mode: str | None = None  # "export" | "import"
    name_csv_arg: str | None = None
    inputs: list[str] = []

    args = argv[1:]
    idx = 0
    while idx < len(args):
        arg = args[idx]
        if arg == "--include-raw":
            include_raw = True
            idx += 1
            continue

        if arg.startswith("--name-export="):
            if name_mode is not None:
                print("[FAIL] use only one of --name-export / --name-import")
                _print_usage()
                return 1
            name_mode = "export"
            name_csv_arg = arg.split("=", 1)[1]
            idx += 1
            continue

        if arg.startswith("--name-import="):
            if name_mode is not None:
                print("[FAIL] use only one of --name-export / --name-import")
                _print_usage()
                return 1
            name_mode = "import"
            name_csv_arg = arg.split("=", 1)[1]
            idx += 1
            continue

        if arg == "--name-export":
            if name_mode is not None:
                print("[FAIL] use only one of --name-export / --name-import")
                _print_usage()
                return 1
            name_mode = "export"
            if (
                idx + 1 < len(args)
                and not args[idx + 1].startswith("--")
                and args[idx + 1].lower().endswith(".csv")
            ):
                name_csv_arg = args[idx + 1]
                idx += 2
            else:
                idx += 1
            continue

        if arg == "--name-import":
            if name_mode is not None:
                print("[FAIL] use only one of --name-export / --name-import")
                _print_usage()
                return 1
            name_mode = "import"
            if (
                idx + 1 < len(args)
                and not args[idx + 1].startswith("--")
                and args[idx + 1].lower().endswith(".csv")
            ):
                name_csv_arg = args[idx + 1]
                idx += 2
            else:
                idx += 1
            continue

        if arg == "--name-csv":
            if idx + 1 >= len(args):
                print("[FAIL] --name-csv requires a csv file path")
                _print_usage()
                return 1
            name_csv_arg = args[idx + 1]
            idx += 2
            continue

        if arg == "--code-to-json":
            mode_code_to_json = True
            idx += 1
            continue

        if arg == "--json-to-code":
            mode_json_to_code = True
            idx += 1
            continue

        if arg == "--dir":
            if idx + 1 >= len(args):
                print("[FAIL] --dir requires a folder path")
                _print_usage()
                return 1
            dir_mode = Path(args[idx + 1].strip('"'))
            idx += 2
            continue

        if arg.startswith("--"):
            print(f"[FAIL] unknown option: {arg}")
            _print_usage()
            return 1

        inputs.append(arg)
        idx += 1

    if name_csv_arg is not None and name_mode is None:
        print("[FAIL] --name-csv is only valid with --name-export or --name-import")
        _print_usage()
        return 1

    if name_mode is not None:
        if mode_code_to_json or mode_json_to_code:
            print("[FAIL] --name-export/--name-import cannot be combined with --code-to-json/--json-to-code")
            _print_usage()
            return 1

        csv_path = _resolve_csv_path(name_csv_arg)

        if dir_mode is not None:
            if inputs:
                print("[FAIL] cannot combine --dir mode with individual file arguments")
                _print_usage()
                return 1
            if not dir_mode.exists():
                print(f"[FAIL] folder not found: {dir_mode}")
                return 1
            if not dir_mode.is_dir():
                print(f"[FAIL] not a folder: {dir_mode}")
                return 1

            ok = (
                _name_export_dir(dir_mode, csv_path)
                if name_mode == "export"
                else _name_import_dir(dir_mode, csv_path)
            )
            return 0 if ok else 1

        if len(inputs) != 1:
            print("[FAIL] name mode requires exactly one <file.code> or --dir <folder>")
            _print_usage()
            return 1

        src = Path(inputs[0].strip('"'))
        ok = (
            _name_export_single(src, csv_path)
            if name_mode == "export"
            else _name_import_single(src, csv_path)
        )
        return 0 if ok else 1

    if dir_mode is not None:
        if inputs:
            print("[FAIL] cannot combine --dir mode with individual file arguments")
            _print_usage()
            return 1

        if mode_code_to_json == mode_json_to_code:
            print("[FAIL] with --dir, specify exactly one of --code-to-json or --json-to-code")
            _print_usage()
            return 1

        if not dir_mode.exists():
            print(f"[FAIL] folder not found: {dir_mode}")
            return 1
        if not dir_mode.is_dir():
            print(f"[FAIL] not a folder: {dir_mode}")
            return 1

        target_suffix = ".code" if mode_code_to_json else ".json"
        files = sorted(
            p for p in dir_mode.iterdir() if p.is_file() and p.suffix.lower() == target_suffix
        )
        if not files:
            print(f"[SKIP] no {target_suffix} files found in: {dir_mode}")
            return 0

        failures = 0
        for src in files:
            if not _convert_single_file(src, include_raw=include_raw):
                failures += 1
        return 1 if failures else 0

    if mode_code_to_json or mode_json_to_code:
        print("[FAIL] --code-to-json / --json-to-code are only valid with --dir")
        _print_usage()
        return 1

    if not inputs:
        _print_usage()
        return 1

    failures = 0
    for raw_arg in inputs:
        src = Path(raw_arg.strip('"'))
        if not _convert_single_file(src, include_raw=include_raw):
            failures += 1

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
