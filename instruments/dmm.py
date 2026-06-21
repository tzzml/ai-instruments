"""UNI-T UT61E digital multimeter serial adapter.

The UT61E optical serial cable streams 14-byte frames continuously.  This
module keeps the hardware-facing part tiny and exposes parsed readings in a
shape that the CLI and Web panel can share.
"""
from __future__ import annotations

import contextlib
import glob
import os
import time
from dataclasses import dataclass
from typing import Iterable, Optional


class DMMError(RuntimeError):
    """UT61E communication or frame parsing error."""


@dataclass(frozen=True)
class DMMReading:
    value: Optional[float]
    units: str
    display: str
    raw_digits: str
    mode: str
    range_index: int
    dc: bool
    ac: bool
    auto: bool
    hold: bool
    relative: bool
    low_battery: bool
    data_valid: bool
    overload: bool
    timestamp: float

    def as_dict(self) -> dict:
        return {
            "value": self.value,
            "units": self.units,
            "display": self.display,
            "raw_digits": self.raw_digits,
            "mode": self.mode,
            "range_index": self.range_index,
            "dc": self.dc,
            "ac": self.ac,
            "auto": self.auto,
            "hold": self.hold,
            "relative": self.relative,
            "low_battery": self.low_battery,
            "data_valid": self.data_valid,
            "overload": self.overload,
            "timestamp": self.timestamp,
        }


# Frame flag bits after subtracting 0x30 from each protocol byte.
SIGN = 0x04
OVERLOAD = 0x02
AC = 0x04
DC = 0x08
AUTO = 0x02
HOLD = 0x01
RELATIVE = 0x01
LOW_BATTERY = 0x02


_MODE_MAP = {
    0x0B: ("V", ["220.00mV", "2.2000V", "22.000V", "220.00V", "1000.0V"]),
    0x0D: ("ohm", ["220.00ohm", "2.2000kohm", "22.000kohm", "220.00kohm", "2.2000Mohm", "22.000Mohm"]),
    0x0F: ("diode", ["2.2000V"]),
    0x03: ("Hz", ["220.00Hz", "2.2000kHz", "22.000kHz", "220.00kHz", "2.2000MHz", "22.000MHz"]),
    0x05: ("F", ["22.000nF", "220.00nF", "2.2000uF", "22.000uF", "220.00uF"]),
    0x00: ("C", ["220.00C"]),
    0x01: ("A", ["220.00uA", "2200.0uA"]),
    0x02: ("A", ["22.000mA", "220.00mA"]),
    0x0A: ("A", ["2.2000A", "10.000A"]),
}

_PREFIX = {
    "p": 1e-12,
    "n": 1e-9,
    "u": 1e-6,
    "m": 1e-3,
    "": 1.0,
    "k": 1e3,
    "M": 1e6,
}


def configured_port(port: Optional[str] = None) -> Optional[str]:
    """Return the requested or environment-configured serial port."""
    return port or os.environ.get("UT61E_PORT") or os.environ.get("DMM_PORT")


def candidate_ports() -> list[str]:
    """Best-effort serial port suggestions for the optical cable."""
    patterns = [
        "/dev/tty.usbserial*",
        "/dev/tty.usbmodem*",
        "/dev/cu.usbserial*",
        "/dev/cu.usbmodem*",
    ]
    ports: list[str] = []
    for pattern in patterns:
        ports.extend(glob.glob(pattern))
    return sorted(dict.fromkeys(ports))


def status(port: Optional[str] = None) -> dict:
    p = configured_port(port)
    return {
        "online": False,
        "configured": bool(p),
        "port": p,
        "candidates": candidate_ports(),
        "state_source": "serial",
        "note": "UT61E 通过光电串口连续输出读数；设置 UT61E_PORT 或在读取时传入 port。",
    }


def parse_frame(frame: bytes | bytearray | Iterable[int], timestamp: Optional[float] = None) -> DMMReading:
    raw = bytes(frame)
    if len(raw) != 14:
        raise DMMError("UT61E 帧长度应为 14 字节，收到 %d 字节" % len(raw))
    values = [b - 0x30 for b in raw]
    if any(v < 0 or v > 0x0F for v in values[:12]):
        raise DMMError("UT61E 帧含非法半字节: %r" % raw)

    mode_code = values[6]
    range_index = values[0]
    mode, ranges = _MODE_MAP.get(mode_code, ("unknown", [""]))
    spec = ranges[min(range_index, len(ranges) - 1)] if ranges else ""
    scale, units, decimals = _range_spec(spec)

    digits = "".join(str(min(9, values[i])) for i in (1, 2, 3, 4, 5))
    overload = bool(values[7] & OVERLOAD)
    signed = bool(values[7] & SIGN)
    data_valid = not overload and mode != "unknown"
    value = None
    if data_valid:
        integer = int(digits)
        value = integer * scale
        if signed:
            value = -value

    display = _format_display(value, units, decimals, overload)
    return DMMReading(
        value=value,
        units=units,
        display=display,
        raw_digits=digits,
        mode=mode,
        range_index=range_index,
        dc=bool(values[10] & DC),
        ac=bool(values[10] & AC),
        auto=bool(values[10] & AUTO),
        hold=bool(values[10] & HOLD),
        relative=bool(values[9] & RELATIVE),
        low_battery=bool(values[11] & LOW_BATTERY),
        data_valid=data_valid,
        overload=overload,
        timestamp=time.time() if timestamp is None else timestamp,
    )


def read_once(port: Optional[str] = None, timeout_s: float = 2.0) -> DMMReading:
    """Read one valid UT61E frame from serial."""
    p = configured_port(port)
    if not p:
        raise DMMError("未配置 UT61E 串口；请设置 UT61E_PORT 或传入 port")
    try:
        import serial
    except Exception as e:  # pragma: no cover - depends on optional runtime import
        raise DMMError("缺少 pyserial: %s" % e) from e

    deadline = time.time() + timeout_s
    last_error: Optional[Exception] = None
    with serial.Serial(
        p,
        baudrate=19200,
        bytesize=serial.SEVENBITS,
        parity=serial.PARITY_ODD,
        stopbits=serial.STOPBITS_ONE,
        timeout=min(timeout_s, 0.5),
        dsrdtr=False,
        rtscts=False,
    ) as ser:
        ser.dtr = True
        ser.rts = False
        buf = bytearray()
        while time.time() < deadline:
            chunk = ser.read(14 - len(buf) if len(buf) < 14 else 1)
            if chunk:
                buf.extend(chunk)
            while len(buf) >= 14:
                candidate = bytes(buf[:14])
                del buf[:14]
                with contextlib.suppress(DMMError):
                    return parse_frame(candidate)
                last_error = DMMError("收到无法解析的 UT61E 帧")
            time.sleep(0.01)
    if last_error:
        raise DMMError("读取 UT61E 超时，最后错误: %s" % last_error)
    raise DMMError("读取 UT61E 超时")


def _range_spec(spec: str) -> tuple[float, str, int]:
    if not spec:
        return 1.0, "", 0
    number = ""
    suffix = spec
    for idx, char in enumerate(spec):
        if char.isdigit() or char == ".":
            number += char
        else:
            suffix = spec[idx:]
            break
    decimals = len(number.split(".", 1)[1]) if "." in number else 0
    prefix = ""
    units = suffix
    if suffix and suffix[0] in _PREFIX and len(suffix) > 1:
        prefix = suffix[0]
        units = suffix[1:]
    scale = _PREFIX.get(prefix, 1.0) / (10 ** decimals)
    return scale, units, decimals


def _format_display(value: Optional[float], units: str, decimals: int, overload: bool) -> str:
    if overload or value is None:
        return ("OL " + units).strip()
    text = ("%." + str(decimals) + "f") % value
    return (text + " " + units).strip()
