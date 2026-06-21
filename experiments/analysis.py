"""Offline analysis helpers for the electromagnetics workbench.

These functions do not touch instruments. They turn sampled data into the
uniform API shape used by the web workbench.
"""
from __future__ import annotations

from typing import Iterable

import numpy as np

C0 = 299_792_458.0


def _response(ok: bool = True, valid: bool = True, warnings=None, raw=None,
              fit=None, metrics=None, next_hint: str = "") -> dict:
    return {
        "ok": bool(ok),
        "valid": bool(valid),
        "warnings": list(warnings or []),
        "raw": raw or {},
        "fit": fit or {},
        "metrics": metrics or {},
        "next_hint": next_hint,
    }


def _interp_crossing(f0: float, y0: float, f1: float, y1: float,
                     threshold: float) -> float:
    if y1 == y0:
        return (f0 + f1) / 2.0
    frac = (threshold - y0) / (y1 - y0)
    return f0 + frac * (f1 - f0)


def q_from_sweep(points: Iterable[tuple[float, float]]) -> dict:
    """Estimate Q from frequency/amplitude points, requiring both -3 dB edges."""
    ordered = sorted((float(f), float(v)) for f, v in points)
    raw = {"sweep": [{"f": f, "v": v} for f, v in ordered]}
    if len(ordered) < 3:
        return _response(False, False, ["至少需要 3 个扫频点"], raw=raw)

    freqs = np.array([p[0] for p in ordered], dtype=float)
    amps = np.array([p[1] for p in ordered], dtype=float)
    peak_idx = int(np.argmax(amps))
    f0 = float(freqs[peak_idx])
    peak = float(amps[peak_idx])
    if peak <= 0:
        return _response(True, False, ["峰值幅度无效，无法计算 -3dB 带宽"], raw=raw)
    threshold = peak / np.sqrt(2.0)

    f1 = None
    for i in range(peak_idx - 1, -1, -1):
        lo = amps[i]
        hi = amps[i + 1]
        if (lo - threshold) * (hi - threshold) <= 0 and lo != hi:
            f1 = _interp_crossing(freqs[i], lo, freqs[i + 1], hi, threshold)
            break

    f2 = None
    for i in range(peak_idx, len(amps) - 1):
        hi = amps[i]
        lo = amps[i + 1]
        if (hi - threshold) * (lo - threshold) <= 0 and hi != lo:
            f2 = _interp_crossing(freqs[i], hi, freqs[i + 1], lo, threshold)
            break

    if f1 is None or f2 is None:
        return _response(
            True,
            False,
            ["未找到完整 -3dB 边界，请扩大扫频范围或降低耦合强度"],
            raw=raw,
            metrics={"f0": f0, "peak_vrms": peak, "threshold": float(threshold)},
            next_hint="向峰值两侧扩大频率范围，直到响应低于峰值的 0.707 倍。",
        )

    bandwidth = float(f2 - f1)
    q = float(f0 / bandwidth) if bandwidth > 0 else float("inf")
    return _response(
        True,
        True,
        [],
        raw=raw,
        metrics={
            "f0": f0,
            "peak_vrms": peak,
            "f1": float(f1),
            "f2": float(f2),
            "bandwidth": bandwidth,
            "q": q,
            "threshold": float(threshold),
        },
        next_hint="可切换到 ring-down，用时域衰减独立验证 Q。",
    )


def analyze_tdr(time_s, voltage_v, velocity_factor: float = 0.66) -> dict:
    """Find incident/reflected pulses and estimate length from round-trip time."""
    t = np.asarray(time_s, dtype=float)
    v = np.asarray(voltage_v, dtype=float)
    raw = {
        "time": t.tolist(),
        "voltage": v.tolist(),
    }
    if len(t) < 5 or len(t) != len(v):
        return _response(False, False, ["TDR 数据长度无效"], raw=raw)

    centered = v - np.median(v)
    mag = np.abs(centered)
    threshold = float(mag.max() * 0.25)
    candidates = np.where(
        (mag[1:-1] >= mag[:-2])
        & (mag[1:-1] >= mag[2:])
        & (mag[1:-1] >= threshold)
    )[0] + 1
    if len(candidates) < 2:
        return _response(True, False, ["未检测到入射/反射两个脉冲"], raw=raw)

    strongest = sorted(candidates, key=lambda i: mag[i], reverse=True)
    first = min(strongest[0], strongest[1])
    second = max(strongest[0], strongest[1])
    # If the two strongest are too close, search for a later distinct pulse.
    min_sep = max(2, len(t) // 100)
    for idx in strongest[1:]:
        if abs(idx - strongest[0]) >= min_sep:
            first = min(strongest[0], idx)
            second = max(strongest[0], idx)
            break

    delta_t = float(t[second] - t[first])
    velocity = C0 * float(velocity_factor)
    length = velocity * delta_t / 2.0
    refl = float(centered[second] / centered[first]) if centered[first] else 0.0
    return _response(
        True,
        delta_t > 0,
        [],
        raw=raw,
        fit={"incident_index": int(first), "reflection_index": int(second)},
        metrics={
            "incident_time_s": float(t[first]),
            "reflection_time_s": float(t[second]),
            "delta_t_s": delta_t,
            "velocity_m_s": velocity,
            "length_m": float(length),
            "reflection_ratio": refl,
        },
        next_hint="改变终端开路/短路/匹配，比较反射极性和幅度。",
    )


def fit_coupling_points(points: Iterable[dict]) -> dict:
    """Fit simple distance exponent and iron-core gain from recorded points."""
    pts = [dict(p) for p in points]
    raw = {"points": pts}
    base = [
        p for p in pts
        if not p.get("core")
        and float(p.get("gain", 0)) > 0
        and float(p.get("distance_cm", 0)) > 0
        and abs(float(p.get("angle_deg", 0))) < 1e-9
    ]
    if len(base) < 2:
        return _response(True, False, ["至少需要两个空心同轴距离点"], raw=raw)

    x = np.log([float(p["distance_cm"]) for p in base])
    y = np.log([float(p["gain"]) for p in base])
    exponent, intercept = np.polyfit(x, y, 1)

    core_ratios = []
    for p in pts:
        if not p.get("core") or float(p.get("gain", 0)) <= 0:
            continue
        pred = float(np.exp(intercept) * float(p["distance_cm"]) ** exponent)
        if pred > 0:
            core_ratios.append(float(p["gain"]) / pred)

    angle_pts = [
        p for p in pts
        if not p.get("core") and float(p.get("gain", 0)) > 0
        and float(p.get("distance_cm", 0)) > 0
    ]
    return _response(
        True,
        True,
        [],
        raw=raw,
        fit={
            "distance_exponent": float(exponent),
            "distance_intercept": float(intercept),
            "core_gain": float(np.median(core_ratios)) if core_ratios else None,
            "angle_model": "gain ∝ cos(theta)",
            "n_angle_points": len(angle_pts),
        },
        metrics={
            "n_points": len(pts),
            "n_core_points": sum(1 for p in pts if p.get("core")),
        },
        next_hint="保持距离不变旋转接收线圈，记录 cos(theta) 曲线。",
    )
