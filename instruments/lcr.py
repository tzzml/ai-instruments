"""UNI-T UT612 LCR meter adapter.

Protocol notes are based on the public reverse-engineered projects
`heyalexej/rusty-UT612` and `optisimon/UT612-linux-software`.

The UT612 exposes a Silicon Labs CP2110 HID USB-to-UART bridge, not a normal
serial port.  The CP2110 is configured to 9600 8N1 via HID feature reports and
then streams 17-byte UT612 measurement frames.
"""
from __future__ import annotations

import contextlib
import time
from dataclasses import dataclass
from typing import Iterable, Optional


VID = 0x10C4
PID = 0xEA80
FRAME_LEN = 17

REPORT_UART_ENABLE = 0x41
UART_CONFIG_9600_8N1 = bytes([0x50, 0x00, 0x00, 0x25, 0x80, 0x00, 0x00, 0x03, 0x00])


class LCRError(RuntimeError):
    """UT612 communication or frame parsing error."""


@dataclass(frozen=True)
class DisplayValue:
    code: str
    raw: int
    decimals: int
    unit: str
    text: str
    value: Optional[float]
    high_flag: bool

    def as_dict(self) -> dict:
        return {
            "code": self.code,
            "raw": self.raw,
            "decimals": self.decimals,
            "unit": self.unit,
            "text": self.text,
            "value": self.value,
            "high_flag": self.high_flag,
        }


@dataclass(frozen=True)
class LCRReading:
    raw_hex: str
    frequency: str
    frequency_hz: Optional[float]
    battery_level: int
    flags: dict
    sorting_tolerance: str
    main_mode: str
    secondary_mode: str
    primary: DisplayValue
    secondary: DisplayValue
    timestamp: float

    @property
    def display(self) -> str:
        parts = [self.main_mode, self.frequency, self.primary.text]
        if self.secondary_mode or self.secondary.text:
            parts.extend(["|", self.secondary_mode, self.secondary.text])
        flag_text = []
        if self.flags.get("hold"):
            flag_text.append("H")
        if self.flags.get("auto") and self.flags.get("lcr"):
            flag_text.append("Auto LCR")
        elif self.flags.get("auto"):
            flag_text.append("Auto")
        elif self.flags.get("lcr"):
            flag_text.append("LCR")
        if self.flags.get("parallel"):
            flag_text.append("PAL")
        if flag_text:
            parts.append("[%s]" % ",".join(flag_text))
        return " ".join(part for part in parts if part)

    def as_dict(self) -> dict:
        return {
            "raw_hex": self.raw_hex,
            "frequency": self.frequency,
            "frequency_hz": self.frequency_hz,
            "battery_level": self.battery_level,
            "flags": dict(self.flags),
            "sorting_tolerance": self.sorting_tolerance,
            "main_mode": self.main_mode,
            "secondary_mode": self.secondary_mode,
            "primary": self.primary.as_dict(),
            "secondary": self.secondary.as_dict(),
            "display": self.display,
            "timestamp": self.timestamp,
        }


def list_devices() -> list[dict]:
    hid = _hidapi()
    out = []
    for dev in hid.enumerate(VID, PID):
        out.append({
            "path": _decode_path(dev.get("path")),
            "manufacturer": dev.get("manufacturer_string"),
            "product": dev.get("product_string"),
            "serial_number": dev.get("serial_number"),
            "interface_number": dev.get("interface_number"),
            "vendor_id": dev.get("vendor_id"),
            "product_id": dev.get("product_id"),
        })
    return out


def status() -> dict:
    try:
        devices = list_devices()
        return {
            "online": bool(devices),
            "configured": True,
            "devices": devices,
            "state_source": "hid",
            "note": "UT612 使用 CP2110 HID USB-to-UART；请在仪表上打开 USB 模式。",
        }
    except Exception as e:
        return {
            "online": False,
            "configured": False,
            "devices": [],
            "state_source": "hid",
            "error": str(e)[:160],
            "note": "需要安装 hidapi Python 包，并允许访问 10c4:ea80 HID 设备。",
        }


def read_once(timeout_s: float = 2.0) -> LCRReading:
    with Cp2110() as dev:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            frames = dev.read_frames(timeout_ms=max(1, int(min(0.25, timeout_s) * 1000)))
            if frames:
                return decode_frame(frames[0])
            time.sleep(0.01)
    raise LCRError("读取 UT612 超时")


def decode_frame(frame: bytes | bytearray | Iterable[int], timestamp: Optional[float] = None) -> LCRReading:
    b = bytes(frame)
    if len(b) != FRAME_LEN:
        raise LCRError("UT612 帧长度应为 17 字节，收到 %d 字节" % len(b))
    if b[0] != 0x00 or b[1] != 0x0D or b[15] != 0x0D or b[16] != 0x0A:
        raise LCRError("UT612 帧标记错误: %s" % hex_string(b))

    parallel = bool(b[2] & 0x80)
    main_mode = _main_mode(b[5] & 0x07, parallel)
    frequency, frequency_hz = _frequency(b[3], main_mode)
    secondary_mode = _secondary_mode(b[10], parallel)
    flags = {
        "hold": bool(b[2] & 0x01),
        "vendor_flag_0x04": bool(b[2] & 0x04),
        "calibration_flag_0x08": bool(b[2] & 0x08),
        "lcr": bool(b[2] & 0x20),
        "auto": bool(b[2] & 0x40),
        "parallel": parallel,
    }
    return LCRReading(
        raw_hex=hex_string(b),
        frequency=frequency,
        frequency_hz=frequency_hz,
        battery_level=(b[3] & 0x18) >> 3,
        flags=flags,
        sorting_tolerance=_sorting_tolerance(b[4] & 0x0F),
        main_mode=main_mode,
        secondary_mode=secondary_mode,
        primary=_display_value(b[6], b[7], b[8], b[9], secondary=False),
        secondary=_display_value(b[11], b[12], b[13], b[14], secondary=True),
        timestamp=time.time() if timestamp is None else timestamp,
    )


def drain_frames(buffer: bytearray) -> list[bytes]:
    frames = []
    while len(buffer) >= FRAME_LEN:
        start = _find_frame_start(buffer)
        if start is None:
            buffer.clear()
            break
        if start:
            del buffer[:start]
        if len(buffer) < FRAME_LEN:
            break
        candidate = bytes(buffer[:FRAME_LEN])
        if candidate[15] == 0x0D and candidate[16] == 0x0A:
            with contextlib.suppress(LCRError):
                decode_frame(candidate)
                frames.append(candidate)
                del buffer[:FRAME_LEN]
                continue
        del buffer[:1]
    return frames


class Cp2110:
    def __init__(self):
        hid = _hidapi()
        self.device = hid.device()
        self.device.open(VID, PID)
        self.buffer = bytearray()
        self.enabled = False
        self.configure()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    def configure(self) -> None:
        self.send_feature(UART_CONFIG_9600_8N1)
        self.set_uart_enabled(True)

    def send_feature(self, report: bytes | bytearray | list[int]) -> None:
        self.device.send_feature_report(bytes(report))

    def set_uart_enabled(self, enabled: bool) -> None:
        self.send_feature(bytes([REPORT_UART_ENABLE, 0x01 if enabled else 0x00]))
        self.enabled = enabled

    def read_frames(self, timeout_ms: int = 1000) -> list[bytes]:
        packet = self.device.read(64, timeout_ms)
        if not packet:
            return []
        uart_len = int(packet[0])
        if uart_len <= 0:
            return []
        available = min(uart_len, max(0, len(packet) - 1))
        self.buffer.extend(bytes(packet[1:1 + available]))
        return drain_frames(self.buffer)

    def close(self) -> None:
        if self.enabled:
            with contextlib.suppress(Exception):
                self.set_uart_enabled(False)
        with contextlib.suppress(Exception):
            self.device.close()


def hex_string(data: bytes | bytearray | Iterable[int]) -> str:
    return " ".join("%02x" % int(b) for b in data)


def _main_mode(raw: int, parallel: bool) -> str:
    if raw == 1:
        return "Lp" if parallel else "Ls"
    if raw == 2:
        return "Cp" if parallel else "Cs"
    if raw == 3:
        return "Rp" if parallel else "Rs"
    if raw == 4:
        return "DCR"
    return "unknown(0x%02x)" % raw


def _secondary_mode(raw: int, parallel: bool) -> str:
    if raw == 0:
        return ""
    if raw == 1:
        return "D"
    if raw == 2:
        return "Q"
    if raw == 3:
        return "Rp" if parallel else "ESR"
    if raw == 4:
        return "theta"
    return "unknown(0x%02x)" % raw


def _frequency(raw: int, main_mode: str) -> tuple[str, Optional[float]]:
    if main_mode == "DCR" or raw == 0xB8:
        return "0Hz", 0.0
    table = {
        0: ("100Hz", 100.0),
        1: ("120Hz", 120.0),
        2: ("1KHz", 1e3),
        3: ("10KHz", 10e3),
        4: ("100KHz", 100e3),
    }
    return table.get((raw & 0xE0) >> 5, ("unknown(0x%02x)" % raw, None))


def _unit(raw: int, secondary: bool) -> str:
    unit = raw >> 3
    table = {
        0: "",
        1: "Ohm",
        2: "kOhm",
        3: "MOhm",
        5: "uH",
        6: "mH",
        7: "H",
        8: "kH",
        9: "pF",
        10: "nF",
        11: "uF",
        12: "mF",
    }
    if secondary and unit == 14:
        return "Deg"
    return table.get(unit, "unknown(0x%02x)" % unit)


def _status(raw: int) -> str:
    table = {
        0: "numeric",
        1: "",
        2: "-",
        3: "OL",
        4: "OFF",
        6: "ERR",
        7: "PASS",
        8: "FAIL",
        9: "OPEN",
        10: "SHORT",
    }
    return table.get(raw & 0x1F, "unknown(0x%02x)" % (raw & 0x1F))


def _sorting_tolerance(raw: int) -> str:
    table = {
        0: "",
        3: "0.25%",
        4: "0.5%",
        5: "1.0%",
        6: "2.0%",
        7: "5.0%",
        8: "10.0%",
        9: "20.0%",
        10: "-20% +80%",
    }
    return table.get(raw, "unknown(0x%02x)" % raw)


def _display_value(msb: int, lsb: int, format_byte: int, status_byte: int, secondary: bool) -> DisplayValue:
    raw = int.from_bytes(bytes([msb, lsb]), "big", signed=True)
    decimals = format_byte & 0x07
    unit = _unit(format_byte, secondary=secondary)
    code = _status(status_byte)
    value = raw / (10 ** decimals)
    if code == "numeric":
        text = _format_value(value, decimals, unit)
    elif code == "":
        text = ""
        value = None
    else:
        text = code
        value = None
    return DisplayValue(
        code=code,
        raw=raw,
        decimals=decimals,
        unit=unit,
        text=text,
        value=value,
        high_flag=bool(status_byte & 0x80),
    )


def _format_value(value: float, decimals: int, unit: str) -> str:
    text = ("%." + str(decimals) + "f") % value
    return (text + " " + unit).strip()


def _find_frame_start(buffer: bytearray) -> Optional[int]:
    for idx in range(len(buffer) - 1):
        if buffer[idx] == 0x00 and buffer[idx + 1] == 0x0D:
            return idx
    return None


def _decode_path(path) -> str:
    if isinstance(path, bytes):
        return path.decode(errors="replace")
    return "" if path is None else str(path)


def _hidapi():
    try:
        import hid
    except Exception as e:  # pragma: no cover - depends on optional package
        raise LCRError("缺少 hidapi Python 包，请安装 requirements.txt 中的 hidapi: %s" % e) from e
    return hid

