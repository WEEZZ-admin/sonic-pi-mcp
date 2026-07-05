from __future__ import annotations

import socket
import struct
import threading
from dataclasses import dataclass
from typing import Any, Callable

from sonic_pi_mcp.errors import SonicPiProtocolError


OSCArg = int | float | str | bytes | bytearray | bool | None


@dataclass(frozen=True)
class OSCMessage:
    address: str
    args: tuple[Any, ...]


def encode_message(address: str, args: list[OSCArg] | tuple[OSCArg, ...] = ()) -> bytes:
    if not address.startswith("/"):
        raise SonicPiProtocolError(f"OSC address must start with '/': {address}")

    type_tags = [","]
    payload = bytearray()
    for arg in args:
        if isinstance(arg, bool):
            type_tags.append("i")
            payload += struct.pack(">i", 1 if arg else 0)
        elif isinstance(arg, int):
            type_tags.append("i")
            payload += struct.pack(">i", arg)
        elif isinstance(arg, float):
            type_tags.append("f")
            payload += struct.pack(">f", arg)
        elif isinstance(arg, str):
            type_tags.append("s")
            payload += _pack_string(arg)
        elif isinstance(arg, (bytes, bytearray)):
            blob = bytes(arg)
            type_tags.append("b")
            payload += struct.pack(">i", len(blob))
            payload += _pad4(blob)
        elif arg is None:
            type_tags.append("N")
        else:
            raise SonicPiProtocolError(f"Unsupported OSC arg type: {type(arg).__name__}")

    return _pack_string(address) + _pack_string("".join(type_tags)) + bytes(payload)


def decode_packet(data: bytes) -> list[OSCMessage]:
    if data.startswith(b"#bundle"):
        return _decode_bundle(data)
    message, offset = _decode_message(data, 0)
    if offset > len(data):
        raise SonicPiProtocolError("OSC decoder overran packet")
    return [message]


def _decode_bundle(data: bytes) -> list[OSCMessage]:
    bundle_tag, offset = _read_string(data, 0)
    if bundle_tag != "#bundle":
        raise SonicPiProtocolError("Invalid OSC bundle")
    offset += 8  # timetag
    messages: list[OSCMessage] = []
    while offset < len(data):
        size = _read_int(data, offset)
        offset += 4
        packet = data[offset : offset + size]
        offset += size
        messages.extend(decode_packet(packet))
    return messages


def _decode_message(data: bytes, offset: int) -> tuple[OSCMessage, int]:
    address, offset = _read_string(data, offset)
    type_tags, offset = _read_string(data, offset)
    if not type_tags.startswith(","):
        raise SonicPiProtocolError(f"Invalid OSC type tag string for {address}: {type_tags}")

    args: list[Any] = []
    for tag in type_tags[1:]:
        if tag == "i":
            args.append(_read_int(data, offset))
            offset += 4
        elif tag == "f":
            args.append(struct.unpack(">f", _read_exact(data, offset, 4))[0])
            offset += 4
        elif tag == "s":
            value, offset = _read_string(data, offset)
            args.append(value)
        elif tag == "b":
            size = _read_int(data, offset)
            offset += 4
            blob = _read_exact(data, offset, size)
            args.append(blob)
            offset += _padded_len(size)
        elif tag == "T":
            args.append(True)
        elif tag == "F":
            args.append(False)
        elif tag == "N":
            args.append(None)
        elif tag == "h":
            args.append(struct.unpack(">q", _read_exact(data, offset, 8))[0])
            offset += 8
        elif tag == "d":
            args.append(struct.unpack(">d", _read_exact(data, offset, 8))[0])
            offset += 8
        else:
            raise SonicPiProtocolError(f"Unsupported OSC type tag '{tag}' in {address}")
    return OSCMessage(address=address, args=tuple(args)), offset


def _pack_string(value: str) -> bytes:
    return _pad4(value.encode("utf-8") + b"\x00")


def _pad4(data: bytes) -> bytes:
    padding = _padded_len(len(data)) - len(data)
    return data + (b"\x00" * padding)


def _padded_len(size: int) -> int:
    return (size + 3) & ~3


def _read_exact(data: bytes, offset: int, size: int) -> bytes:
    end = offset + size
    if end > len(data):
        raise SonicPiProtocolError("Truncated OSC packet")
    return data[offset:end]


def _read_int(data: bytes, offset: int) -> int:
    return struct.unpack(">i", _read_exact(data, offset, 4))[0]


def _read_string(data: bytes, offset: int) -> tuple[str, int]:
    try:
        end = data.index(b"\x00", offset)
    except ValueError as exc:
        raise SonicPiProtocolError("Unterminated OSC string") from exc
    raw = data[offset:end]
    next_offset = _padded_len(end + 1)
    return raw.decode("utf-8", errors="replace"), next_offset


class OSCClient:
    def __init__(self, host: str = "127.0.0.1") -> None:
        self._host = host
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def send(self, port: int, address: str, *args: OSCArg) -> None:
        packet = encode_message(address, args)
        self._socket.sendto(packet, (self._host, port))

    def close(self) -> None:
        self._socket.close()


OSCHandler = Callable[[OSCMessage, tuple[str, int]], None]


class OSCUDPServer:
    def __init__(self, port: int, handler: OSCHandler, host: str = "127.0.0.1") -> None:
        self._port = port
        self._handler = handler
        self._host = host
        self._socket: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    @property
    def port(self) -> int:
        if self._socket:
            return int(self._socket.getsockname()[1])
        return self._port

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.bind((self._host, self._port))
        self._socket.settimeout(0.25)
        self._stop.clear()
        self._thread = threading.Thread(target=self._serve, name="sonic-pi-osc-server", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._socket:
            try:
                self._socket.sendto(b"", (self._host, self.port))
            except OSError:
                pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        if self._socket:
            self._socket.close()
            self._socket = None

    def _serve(self) -> None:
        assert self._socket is not None
        while not self._stop.is_set():
            try:
                data, addr = self._socket.recvfrom(65536)
            except TimeoutError:
                continue
            except OSError:
                break
            if not data:
                continue
            try:
                for message in decode_packet(data):
                    self._handler(message, addr)
            except Exception:
                # The session owns user-visible errors. Keep the UDP loop alive.
                continue

