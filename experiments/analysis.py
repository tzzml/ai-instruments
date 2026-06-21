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


def _edge_arrival_time(time_s, voltage_v):
    t = np.asarray(time_s, dtype=float)
    v = np.asarray(voltage_v, dtype=float)
    if len(t) < 5 or len(t) != len(v):
        return None

    head = max(3, len(v) // 20)
    tail = max(3, len(v) // 20)
    start = float(np.median(v[:head]))
    end = float(np.median(v[-tail:]))
    span = end - start
    if abs(span) < max(float(np.ptp(v)) * 0.1, 1e-9):
        vmin = float(np.min(v))
        vmax = float(np.max(v))
        span = vmax - vmin
        start = vmin
    if abs(span) <= 1e-12:
        return None

    y = (v - start) / span
    threshold = 0.5
    if span > 0:
        crossings = np.where((y[:-1] < threshold) & (y[1:] >= threshold))[0]
    else:
        crossings = np.where((y[:-1] > threshold) & (y[1:] <= threshold))[0]
    if len(crossings) == 0:
        return None

    i = int(crossings[0])
    return float(_interp_crossing(t[i], y[i], t[i + 1], y[i + 1], threshold))


def analyze_propagation_4ch(time_s, channels: dict, distances_m) -> dict:
    """Estimate propagation velocity from edge arrival time at several taps."""
    t = np.asarray(time_s, dtype=float)
    distances = [float(d) for d in distances_m]
    raw = {"distances_m": distances, "arrivals": []}
    if len(t) < 5:
        return _response(False, False, ["传播实验时间轴数据不足"], raw=raw)
    if len(channels) < 2 or len(distances) != len(channels):
        return _response(False, False, ["通道数量和探头位置数量不一致"], raw=raw)

    arrivals = []
    warnings = []
    ordered = sorted((int(ch), np.asarray(v, dtype=float)) for ch, v in channels.items())
    for idx, (ch, voltage) in enumerate(ordered):
        if idx >= len(distances):
            break
        arrival = _edge_arrival_time(t, voltage)
        if arrival is None:
            warnings.append(f"CH{ch} 未检测到清晰波前")
            continue
        arrivals.append({"channel": ch, "distance_m": distances[idx], "arrival_s": arrival})

    raw["arrivals"] = arrivals
    if len(arrivals) < 3:
        return _response(
            True,
            False,
            warnings or ["至少需要 3 个通道检测到到达时间才能拟合传播速度"],
            raw=raw,
            metrics={"arrivals": arrivals},
            next_hint="增大方波幅度或调整触发/垂直档位，让四个通道都能看到同一上升沿。",
        )

    fit_t = np.array([p["arrival_s"] for p in arrivals], dtype=float)
    fit_d = np.array([p["distance_m"] for p in arrivals], dtype=float)
    velocity, intercept = np.polyfit(fit_t, fit_d, 1)
    first = arrivals[0]["arrival_s"]
    delay_s = [float(p["arrival_s"] - first) for p in arrivals]
    return _response(
        True,
        bool(velocity > 0),
        warnings,
        raw=raw,
        fit={
            "method": "distance = velocity * arrival_time + intercept",
            "velocity_m_s": float(velocity),
            "intercept_m": float(intercept),
        },
        metrics={
            "arrivals": arrivals,
            "delay_s": delay_s,
            "velocity_m_s": float(velocity),
            "velocity_factor": float(velocity / C0),
            "arrival_span_s": float(max(fit_t) - min(fit_t)),
            "distance_span_m": float(max(fit_d) - min(fit_d)),
        },
        next_hint="改变导线形态或换同轴线重复测量，比较速度因子和波形畸变。",
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


def _fundamental_phasor(time_s, voltage_v, frequency_hz: float) -> complex:
    t = np.asarray(time_s, dtype=float)
    v = np.asarray(voltage_v, dtype=float)
    if len(t) != len(v) or len(t) < 8:
        raise ValueError("波形长度不足或时间/电压长度不一致")
    v = v - np.mean(v)
    basis = np.column_stack([
        np.cos(2 * np.pi * frequency_hz * t),
        np.sin(2 * np.pi * frequency_hz * t),
    ])
    coef, *_ = np.linalg.lstsq(basis, v, rcond=None)
    return complex(float(coef[0]), -float(coef[1]))


def analyze_impedance_point(
    time_s,
    v_ref,
    v_dut,
    *,
    rsense_ohm: float,
    frequency_hz: float,
    component_hint: str = "",
) -> dict:
    """Estimate DUT impedance using AWG -> R_sense -> DUT -> ground.

    v_ref is the voltage before R_sense, v_dut is the DUT top node. Current is
    (v_ref - v_dut) / R_sense. The impedance is the fundamental phasor ratio
    V_dut / I at the configured sine frequency.
    """
    t = np.asarray(time_s, dtype=float)
    ref = np.asarray(v_ref, dtype=float)
    dut = np.asarray(v_dut, dtype=float)
    current = (ref - dut) / float(rsense_ohm)
    raw = {"time": t.tolist(), "ref": ref.tolist(), "dut": dut.tolist(), "current": current.tolist()}
    if len(t) < 8 or len(ref) != len(t) or len(dut) != len(t):
        return _response(False, False, ["阻抗测量波形长度无效"], raw=raw)

    i_ph = _fundamental_phasor(t, current, float(frequency_hz))
    v_ph = _fundamental_phasor(t, dut, float(frequency_hz))
    i_amp = abs(i_ph)
    if i_amp <= 1e-12:
        return _response(True, False, ["采样电阻电流过小，无法可靠计算阻抗"], raw=raw)

    z = v_ph / i_ph
    z_abs = abs(z)
    phase = float(np.degrees(np.angle(z)))
    omega = 2 * np.pi * float(frequency_hz)
    metrics = {
        "frequency_hz": float(frequency_hz),
        "rsense_ohm": float(rsense_ohm),
        "impedance_ohm": float(z_abs),
        "resistance_ohm": float(z.real),
        "reactance_ohm": float(z.imag),
        "phase_deg": phase,
        "current_amp_a": float(i_amp),
        "dut_voltage_amp_v": float(abs(v_ph)),
    }
    hint = component_hint.lower()
    if "cap" in hint or "电容" in hint:
        x = abs(z.imag) if abs(z.imag) > 1e-12 else z_abs
        metrics["capacitance_f"] = float(1.0 / (omega * x))
    if "ind" in hint or "电感" in hint:
        x = abs(z.imag) if abs(z.imag) > 1e-12 else z_abs
        metrics["inductance_h"] = float(x / omega)

    return _response(
        True,
        True,
        [],
        raw=raw,
        fit={"method": "fundamental phasor at configured sine frequency"},
        metrics=metrics,
        next_hint="改变频率重复记录，观察 |Z| 和相位随频率变化。",
    )
