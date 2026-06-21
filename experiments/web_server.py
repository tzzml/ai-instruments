"""
电磁学交互式学习平台 — Web App 后端

提供 REST API 驱动仪器并返回实验数据，前端 HTML 做可视化。
运行: python -m experiments.web_server
然后打开 http://localhost:8050
"""
from __future__ import annotations

import json
import os
import http.server
import urllib.parse
from pathlib import Path
import numpy as np
from instruments import _backend as bk
from instruments import awg, dmm, lcr, scope
from experiments.analysis import (
    analyze_impedance_point,
    analyze_propagation_4ch,
    analyze_tdr,
    fit_coupling_points,
    q_from_sweep,
)
from experiments.profiles import COURSE_MODULES, EXPERIMENT_PROFILES, EXPERIMENT_STATIONS
from experiments.q_measure import measure_q


PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATIC_ROOT = PROJECT_ROOT / "experiments" / "static"


AWG_PANEL_STATE = {
    1: {"channel": 1, "wave": "sine", "freq": 1000.0, "amp": 2.0, "offset": 0.0, "load": 10000.0, "output": False},
    2: {"channel": 2, "wave": "sine", "freq": 1000.0, "amp": 2.0, "offset": 0.0, "load": 10000.0, "output": False},
}


class ExperimentAPI(http.server.SimpleHTTPRequestHandler):
    """实验 REST API + 静态文件服务"""

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_HEAD(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path.startswith("/api/"):
            self.send_error(405, "HEAD not supported for API endpoints")
            return
        self._serve_static(parsed.path, include_body=False)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        params = urllib.parse.parse_qs(parsed.query)

        if path == "/api/scope/screenshot":
            img = scope.screenshot("PNG")
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.end_headers()
            self.wfile.write(img)
        elif path == "/api/awg/screenshot":
            img = awg.screenshot()
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.end_headers()
            self.wfile.write(img)
        elif path.startswith("/api/"):
            try:
                self._json(self._handle_get_api(path, params))
            except Exception as e:
                self._json({"ok": False, "valid": False, "warnings": [str(e)],
                            "raw": {}, "fit": {}, "metrics": {}, "next_hint": ""})
        else:
            self._serve_static(path)

    def _serve_static(self, path: str, include_body: bool = True):
        # 静态文件: / → index.html, 其他从 experiments/static/ 目录服务
        if path == "/" or path == "":
            path = "/index.html"
        file_path = STATIC_ROOT / path.lstrip("/")
        try:
            content = file_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", _content_type(file_path))
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            if include_body:
                self.wfile.write(content)
        except FileNotFoundError:
            self.send_error(404, "File not found")

    def _handle_get_api(self, path, params):
        if path == "/api/status":
            out = {
                "awg": {
                    "online": True,
                    "id": "write-only",
                    "note": "UTG962 USBTMC 查询不稳定，状态页不发送 *IDN?",
                }
            }
            try:
                out["scope"] = {"online": True, "id": bk.idn("scope")}
            except Exception as e:
                out["scope"] = {"online": False, "error": str(e)[:80]}
            return out

        if path == "/api/panel/status":
            return _panel_status()

        if path == "/api/dmm/status":
            return {"ok": True, "dmm": dmm.status(params.get("port", [None])[0])}

        if path == "/api/lcr/status":
            return {"ok": True, "lcr": lcr.status()}

        if path == "/api/awg/screenshot":
            return {"content_type": "image/png", "bytes": awg.screenshot()}

        if path == "/api/experiments/profiles":
            return {
                "ok": True,
                "valid": True,
                "warnings": [],
                "raw": {
                    "profiles": EXPERIMENT_PROFILES,
                    "modules": COURSE_MODULES,
                    "stations": EXPERIMENT_STATIONS,
                },
                "fit": {},
                "metrics": {},
                "next_hint": "选择一个模块，然后按接线图完成半自动采样。",
            }

        if path == "/api/scope/stats":
            return scope.waveform_stats(1)

        if path == "/api/scope/freq":
            return {"freq": scope.measure_freq(1)}

        if path == "/api/scope/waveforms":
            chs = [int(c) for c in params.get("ch", ["1"])[0].split(",") if c]
            target = int(params.get("points", ["2000"])[0])
            data = scope.get_waveforms(chs)
            out = {}
            sample_int = None
            for ch, (t, v) in data.items():
                if sample_int is None and len(t) > 1:
                    sample_int = float(t[1] - t[0])
                ti, vi = _decimate(t, v, target)
                out[str(ch)] = {
                    "time": ti, "voltage": vi,
                    "n_raw": int(len(v)),
                    "vpp": float(v.max() - v.min()),
                    "vrms": float(np.sqrt(np.mean(v ** 2))),
                    "vmax": float(v.max()), "vmin": float(v.min()),
                }
            return {"channels": out, "sample_int": sample_int}

        if path == "/api/q-measure":
            result = _run_q_sweep({
                "f_start": float(params.get("f_start", [535e3])[0]),
                "f_stop": float(params.get("f_stop", [1605e3])[0]),
                "fine_points": 50,
                "amplitude_vpp": 1,
                "scope_vdiv": 0.5,
                "load_ohm": 50,
                "settle_s": 0.2,
            })
            m = result["metrics"]
            return {
                "valid": result["valid"],
                "warnings": result["warnings"],
                "f0": m.get("f0"),
                "q": m.get("q"),
                "bandwidth": m.get("bandwidth"),
                "f1": m.get("f1"),
                "f2": m.get("f2"),
                "peak_vrms": m.get("peak_vrms"),
                "sweep": result["raw"].get("sweep", []),
            }

        return {"ok": False, "valid": False, "warnings": ["未知 GET 端点: %s" % path],
                "raw": {}, "fit": {}, "metrics": {}, "next_hint": ""}

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}
        try:
            self._json(self._handle_post(parsed.path, body))
        except Exception as e:
            self._json({"ok": False, "error": str(e)})

    def _handle_post(self, path, body):
        # ---- 全局重置: AWG + 示波器回到安全初始态 ----
        if path == "/api/reset":
            return _reset_instruments()
        # ---- AWG: 细粒度, 只改提交的字段 (不重置未提交项) ----
        if path == "/api/awg/configure":
            ch = int(body.get("channel", 1))
            if "wave" in body:          awg.set_wave(ch, body["wave"])
            if "load" in body:          awg.set_load(ch, float(body["load"]))
            if "freq" in body:          awg.set_frequency(ch, float(body["freq"]))
            if "period" in body:        awg.set_period(ch, float(body["period"]))
            if "amp" in body:           awg.set_amplitude(ch, float(body["amp"]), body.get("amp_unit", "vpp"))
            if "offset" in body:        awg.set_offset(ch, float(body["offset"]))
            if "duty" in body:          awg.set_duty(ch, float(body["duty"]))
            if "phase" in body:         awg.set_phase(ch, float(body["phase"]))
            if "ramp_symmetry" in body: awg.set_ramp_symmetry(ch, float(body["ramp_symmetry"]))
            if "output" in body:        awg.output(ch, bool(body["output"]))
            _update_awg_panel_state(ch, body)
            return {"ok": True}
        if path == "/api/awg/configure-am":
            ch = int(body.get("channel", 1))
            awg.configure_am(
                ch,
                carrier_freq=float(body.get("carrier_hz", 1e6)),
                carrier_amp=float(body.get("amp", 2.0)),
                mod_freq=float(body.get("mod_freq_hz", 1000.0)),
                mod_depth=float(body.get("mod_depth_pct", 50.0)),
                load=float(body.get("load", 10000.0)),
            )
            _update_awg_panel_state(ch, {
                "wave": "sine",
                "freq": float(body.get("carrier_hz", 1e6)),
                "amp": float(body.get("amp", 2.0)),
                "load": float(body.get("load", 10000.0)),
                "output": True,
                "modulation": "AM",
                "mod_freq_hz": float(body.get("mod_freq_hz", 1000.0)),
                "mod_depth_pct": float(body.get("mod_depth_pct", 50.0)),
            })
            return {"ok": True}
        if path == "/api/awg/output":
            ch = int(body.get("channel", 1))
            on = bool(body.get("on", True))
            awg.output(ch, on)
            AWG_PANEL_STATE.setdefault(ch, {"channel": ch})["output"] = on
            return {"ok": True}
        if path == "/api/awg/off":
            ch = int(body.get("channel", 1))
            awg.output_off(ch)
            AWG_PANEL_STATE.setdefault(ch, {"channel": ch})["output"] = False
            return {"ok": True}

        # ---- Scope: 采集启停 ----
        if path == "/api/scope/autoset":
            scope.autoset()
            return {"ok": True, "next_hint": "等待示波器自动调整完成后刷新状态或抓取屏幕核对。"}
        if path == "/api/scope/run":
            scope.run(); return {"ok": True}
        if path == "/api/scope/stop":
            scope.stop(); return {"ok": True}
        if path == "/api/scope/single":
            ch = int(body.get("channel", 1))
            ok = scope.acquire_single(
                ch, float(body.get("level", 0)),
                body.get("slope", "POS"),
                float(body.get("timeout", 5.0)))
            return {"ok": ok, "triggered": ok}

        # ---- DMM: UT61E 万用表 ----
        if path == "/api/dmm/read":
            port = body.get("port") or None
            reading = dmm.read_once(port, float(body.get("timeout", 2.0))).as_dict()
            return {"ok": True, "valid": reading["data_valid"], "reading": reading}

        if path == "/api/lcr/read":
            reading = lcr.read_once(float(body.get("timeout", 2.0))).as_dict()
            return {"ok": True, "valid": reading["primary"]["code"] == "numeric", "reading": reading}

        # ---- Scope: 配置 (只改提交字段) ----
        if path == "/api/scope/config":
            ch = int(body.get("channel", 1))
            if "on" in body:            scope.channel_on(ch, bool(body["on"]))
            if "vdiv" in body:          scope.set_scale(ch, float(body["vdiv"]))
            if "offset" in body:        scope.set_offset(ch, float(body["offset"]))
            if "coupling" in body:      scope.set_coupling(ch, body["coupling"])
            if "probe" in body:         scope.set_probe(ch, float(body["probe"]))
            if "tdiv" in body:          scope.set_timebase(float(body["tdiv"]))
            if "srate" in body:         scope.set_srate(float(body["srate"]))
            if "mdepth" in body:        scope.set_mdepth(body["mdepth"])
            if "acquire_mode" in body:  scope.set_acquire_mode(body["acquire_mode"])
            if "trig_mode" in body:     scope.trigger_mode(body["trig_mode"])
            if "trig_level" in body:    scope.set_trigger_edge(
                ch, float(body["trig_level"]), body.get("trig_slope", "POS"))
            return {"ok": True}

        # ---- 一键实验 ----
        if path in EXPERIMENT_POST_ROUTES:
            return EXPERIMENT_POST_ROUTES[path](body)
        return {"ok": False, "error": "未知 POST 端点: %s" % path}

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json(self, data):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self._cors()
        self.end_headers()
        # numpy 类型转 Python 原生
        self.wfile.write(json.dumps(data, default=_json_default).encode())


def _decimate(t, v, target):
    """同步均匀抽取 (t, v) 到约 target 点。点数不足时原样返回。

    用于把示波器长记录 (可达数 M 点) 压缩到前端 canvas 可绘的规模。
    均匀抽取对慢变/周期信号足够; 若需保窄峰, 调用方加大 target。
    """
    n = len(v)
    if n <= target or target <= 0:
        return t, v
    step = max(1, n // target)
    return t[::step], v[::step]


def _update_awg_panel_state(channel, body):
    state = AWG_PANEL_STATE.setdefault(int(channel), {"channel": int(channel)})
    for key in ("wave", "freq", "period", "amp", "offset", "duty", "phase", "load", "output", "ramp_symmetry"):
        if key in body:
            state[key] = body[key]
    state["channel"] = int(channel)


def _panel_status():
    out = {
        "ok": True,
        "awg": {
            "online": True,
            "id": "write-only",
            "state_source": "last_command",
            "note": "UTG962 USBTMC 查询不稳定；面板不查询 AWG，只显示本 Web 会话最近下发的配置。",
            "channels": [dict(AWG_PANEL_STATE.get(ch, {"channel": ch})) for ch in (1, 2)],
        },
    }
    try:
        sc = scope.panel_status()
        sc["state_source"] = "instrument_query"
        out["scope"] = sc
    except Exception as e:
        out["scope"] = {
            "online": False,
            "state_source": "instrument_query",
            "error": str(e)[:120],
            "channels": [{"channel": ch, "error": str(e)[:80]} for ch in range(1, 5)],
        }
    try:
        out["dmm"] = dmm.status()
    except Exception as e:
        out["dmm"] = {
            "online": False,
            "configured": False,
            "state_source": "serial",
            "error": str(e)[:120],
        }
    try:
        out["lcr"] = lcr.status()
    except Exception as e:
        out["lcr"] = {
            "online": False,
            "configured": False,
            "state_source": "hid",
            "error": str(e)[:120],
        }
    return out


def _run_q_sweep(body):
    scope_vdiv = body.get("scope_vdiv", body.get("vdiv"))
    result = measure_q(
        f_start=float(body.get("f_start", 535e3)),
        f_stop=float(body.get("f_stop", 1605e3)),
        coarse_points=int(body.get("coarse_points", body.get("coarse", 50))),
        coarse_step_hz=float(body["coarse_step_hz"]) if body.get("coarse_step_hz") else None,
        q_min=float(body.get("q_min", 30)),
        fine_points=int(body.get("fine_points", body.get("fine", 50))),
        amplitude_vpp=float(body.get("amplitude_vpp", body.get("amp", 1.0))),
        scope_channel=int(body.get("scope_channel", body.get("channel", 1))),
        scope_vdiv=float(scope_vdiv) if scope_vdiv is not None else None,
        capacitance_f=float(body["capacitance_f"]) if body.get("capacitance_f") else None,
        load_ohm=float(body.get("load_ohm", body.get("load", 50))),
        settle_s=float(body.get("settle_s", body.get("settle", 0.2))),
    )
    analysis = q_from_sweep([(p.freq, p.vrms) for p in result.sweep])
    if not result.valid:
        analysis["valid"] = False
        analysis["warnings"] = list(result.warnings or analysis["warnings"])
        analysis["metrics"].update({
            "f0": result.f0,
            "peak_vrms": result.peak_vrms,
        })
    if result.inductance_h is not None:
        analysis["metrics"]["inductance_h"] = result.inductance_h
    return analysis


def _run_ringdown(body):
    from experiments.lc_ringdown import measure_ringdown
    r = measure_ringdown(
        f0_guess=float(body.get("f0_guess", 806e3)),
        awg_freq=float(body.get("awg_freq", 2000)),
        awg_amp=float(body.get("awg_amp", 4.0)),
        scope_channel=int(body.get("channel", 1)),
        trig_level=float(body.get("level", 1.0)),
        cycles=int(body.get("cycles", 20)))
    ti, vi = _decimate(r.time, r.voltage, int(body.get("points", 3000)))
    return {
        "ok": True, "valid": True, "warnings": [],
        "raw": {"time": ti, "voltage": vi,
                "peak_t": r.peak_times, "peak_v": r.peak_volts},
        "fit": {"method": "ln(V_peak) vs time"},
        "metrics": {"f0": r.f0, "tau_d": r.tau_d, "q": r.q},
        "next_hint": "切换到扫频法，比较 Q=f0/BW 与 Q=pi*f0*tau。",
    }


def _record_coupling_point(body):
    """Record/analyze a semi-automatic coupling point.

    If the caller supplies a `points` list, fit the whole set. Otherwise sample
    CH1/CH2 once and return one record the browser can append to its table.
    """
    if body.get("points"):
        return fit_coupling_points(body["points"])

    ch_ref = int(body.get("ref_channel", 1))
    ch_rx = int(body.get("rx_channel", 2))
    data = scope.get_waveforms([ch_ref, ch_rx])
    t_ref, v_ref = data[ch_ref]
    _t_rx, v_rx = data[ch_rx]
    ref = float(np.sqrt(np.mean(np.asarray(v_ref) ** 2)))
    rx = float(np.sqrt(np.mean(np.asarray(v_rx) ** 2)))
    gain = rx / ref if ref else 0.0
    point = {
        "distance_cm": float(body.get("distance_cm", 0)),
        "angle_deg": float(body.get("angle_deg", 0)),
        "core": bool(body.get("core", False)),
        "frequency_hz": float(body.get("frequency_hz", 0)),
        "ref_vrms": ref,
        "rx_vrms": rx,
        "gain": gain,
    }
    t_dec, ref_dec = _decimate(t_ref, v_ref, int(body.get("points_out", 1500)))
    _, rx_dec = _decimate(t_ref, v_rx, int(body.get("points_out", 1500)))
    return {
        "ok": True,
        "valid": True,
        "warnings": [],
        "raw": {"point": point, "time": t_dec, "ref": ref_dec, "rx": rx_dec},
        "fit": {},
        "metrics": point,
        "next_hint": "移动线圈或插拔铁芯后再次记录，累计点数后拟合距离指数和铁芯增益。",
    }


def _record_transformer_point(body):
    ch_primary = int(body.get("primary_channel", 1))
    ch_secondary = int(body.get("secondary_channel", 2))
    data = scope.get_waveforms([ch_primary, ch_secondary])
    t, vp = data[ch_primary]
    _ts, vs = data[ch_secondary]
    vp = np.asarray(vp, dtype=float)
    vs = np.asarray(vs, dtype=float)
    primary = float(np.sqrt(np.mean(vp ** 2)))
    secondary = float(np.sqrt(np.mean(vs ** 2)))
    ratio = secondary / primary if primary else 0.0
    corr = np.correlate(vp - vp.mean(), vs - vs.mean(), mode="full")
    lag = int(np.argmax(corr) - (len(vp) - 1))
    dt = float(t[1] - t[0]) if len(t) > 1 else 0.0
    phase_lag_s = lag * dt
    t_dec, vp_dec = _decimate(t, vp, int(body.get("points_out", 1500)))
    _, vs_dec = _decimate(t, vs, int(body.get("points_out", 1500)))
    return {
        "ok": True,
        "valid": True,
        "warnings": [],
        "raw": {"time": t_dec, "primary": vp_dec, "secondary": vs_dec},
        "fit": {"phase_lag_s": phase_lag_s},
        "metrics": {
            "primary_vrms": primary,
            "secondary_vrms": secondary,
            "voltage_ratio": ratio,
            "core": bool(body.get("core", False)),
            "load_ohm": body.get("load_ohm"),
        },
        "next_hint": "给次级加负载并重复记录，观察次级电压下降和耦合能量转移。",
    }


def _capture_impedance_point(body):
    ref_ch = int(body.get("ref_channel", 1))
    dut_ch = int(body.get("dut_channel", 2))
    data = scope.get_waveforms([ref_ch, dut_ch])
    t_ref, v_ref = data[ref_ch]
    _t_dut, v_dut = data[dut_ch]
    result = analyze_impedance_point(
        t_ref,
        v_ref,
        v_dut,
        rsense_ohm=float(body.get("rsense_ohm", body.get("rsense", 1000.0))),
        frequency_hz=float(body.get("frequency_hz", 1000.0)),
        component_hint=str(body.get("component_hint", "")),
    )
    target = int(body.get("points_out", 1800))
    ti, ref_dec = _decimate(t_ref, np.asarray(v_ref), target)
    _, dut_dec = _decimate(t_ref, np.asarray(v_dut), target)
    _, cur_dec = _decimate(t_ref, (np.asarray(v_ref) - np.asarray(v_dut)) / float(body.get("rsense_ohm", body.get("rsense", 1000.0))), target)
    result["raw"].update({"time": ti, "ref": ref_dec, "dut": dut_dec, "current": cur_dec})
    result["next_hint"] = "换频率再次记录，比较电容 |Z| 随频率下降、电感 |Z| 随频率上升。"
    return result


def _capture_tdr(body):
    if body.get("time") is not None and body.get("voltage") is not None:
        return analyze_tdr(body["time"], body["voltage"], float(body.get("velocity_factor", 0.66)))

    channel = int(body.get("channel", 1))
    t, v = scope.get_waveform(channel)
    return analyze_tdr(t, v, float(body.get("velocity_factor", 0.66)))


def _parse_float_list(value, default):
    if value is None or value == "":
        return list(default)
    if isinstance(value, str):
        return [float(part.strip()) for part in value.split(",") if part.strip()]
    return [float(part) for part in value]


def _capture_propagation_4ch(body):
    channels = [int(ch) for ch in body.get("channels", [1, 2, 3, 4])]
    distances = _parse_float_list(body.get("tap_distances_m"), [0, 10, 20, 30])
    data = scope.get_waveforms(channels)
    first_ch = channels[0]
    t, _ = data[first_ch]
    channel_voltages = {ch: data[ch][1] for ch in channels}
    result = analyze_propagation_4ch(t, channel_voltages, distances)

    target = int(body.get("points_out", 1800))
    raw_channels = {}
    for ch in channels:
        ti, vi = _decimate(data[ch][0], data[ch][1], target)
        raw_channels[str(ch)] = {
            "time": ti,
            "voltage": vi,
            "vpp": float(np.max(data[ch][1]) - np.min(data[ch][1])),
        }
    result["raw"].update({"channels": raw_channels})
    return result


def _capture_diode_va(body):
    from experiments.diode_va import measure_diode_va
    r = measure_diode_va(
        v_scan=float(body.get("v_scan", 5.0)),
        period_s=float(body.get("period_s", 10.0)),
        rsense_ohm=float(body.get("rsense", 1000.0)),
        v_ch=int(body.get("v_ch", 1)),
        i_ch=int(body.get("i_ch", 2)),
        vdiv=body.get("vdiv"))
    n = int(body.get("points", 3000))
    v_dec, i_dec = _decimate(r.voltage, r.current * 1e3, n)
    return {
        "ok": True, "valid": True, "warnings": [],
        "raw": {"voltage": v_dec, "current": i_dec},
        "fit": {},
        "metrics": {"rsense": r.rsense_ohm, "period_s": r.period_s},
        "next_hint": "改变扫描速度，比较准静态曲线是否重合。",
    }


EXPERIMENT_POST_ROUTES = {
    "/api/exp/q-sweep": _run_q_sweep,
    "/api/exp/ringdown": _run_ringdown,
    "/api/exp/coupling-point": _record_coupling_point,
    "/api/exp/transformer-point": _record_transformer_point,
    "/api/exp/tdr-capture": _capture_tdr,
    "/api/exp/propagation-4ch": _capture_propagation_4ch,
    "/api/exp/impedance-point": _capture_impedance_point,
    "/api/exp/diode-va": _capture_diode_va,
}


def _reset_instruments():
    """把 AWG + 示波器置为安全初始态 (软重置, 非 *RST, 保留连接)。

    AWG  : 两通道关输出 + 默认正弦 1 kHz / 2 Vpp / 偏置 0 / 高阻。
    Scope: STOP (冻结) + YT 模式 + AUTO 触发 + CH1 开 / DC / 1 V·div / 1 ms·div。
    每台仪器独立 try, 一台失败不阻塞另一台。
    """
    result = {}
    try:
        awg.output_off(1); awg.output_off(2)
        AWG_PANEL_STATE[1]["output"] = False
        AWG_PANEL_STATE[2]["output"] = False
        awg.configure(1, wave="sine", frequency=1e3, amplitude=2.0,
                      offset=0, load=10000, output=False)
        _update_awg_panel_state(1, {
            "wave": "sine", "freq": 1e3, "amp": 2.0,
            "offset": 0, "load": 10000, "output": False,
        })
        result["awg"] = {"ok": True}
    except Exception as e:
        result["awg"] = {"ok": False, "error": str(e)[:120]}
    try:
        scope.stop()
        scope.set_acquire_mode("YT")
        scope.trigger_mode("AUTO")
        scope.channel_on(1, True)
        scope.set_coupling(1, "DC")
        scope.set_scale(1, 1.0)
        scope.set_offset(1, 0.0)
        scope.set_timebase(1e-3)
        result["scope"] = {"ok": True}
    except Exception as e:
        result["scope"] = {"ok": False, "error": str(e)[:120]}
    return result


def _content_type(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    return {
        ".html": "text/html; charset=utf-8",
        ".css": "text/css",
        ".js": "application/javascript",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".svg": "image/svg+xml",
        ".json": "application/json",
    }.get(ext, "application/octet-stream")


def _json_default(obj):
    import numpy as np
    if isinstance(obj, (np.integer,)): return int(obj)
    if isinstance(obj, (np.floating,)): return float(obj)
    if isinstance(obj, np.ndarray): return obj.tolist()
    raise TypeError


def main(port=8050):
    import sys
    os.chdir(PROJECT_ROOT)
    server = http.server.ThreadingHTTPServer(("0.0.0.0", port), ExperimentAPI)
    print(f"电磁学实验平台: http://localhost:{port}")
    print("API 端点:")
    print("  GET  /api/scope/stats      — 示波器波形统计")
    print("  GET  /api/scope/freq       — 测频率")
    print("  GET  /api/scope/screenshot — 示波器截图")
    print("  GET  /api/q-measure?f_start=535k&f_stop=1605k — Q 值扫频")
    print("  POST /api/awg/configure    — 配置信号发生器")
    print("  POST /api/awg/off           — 关 AWG 输出")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n停止服务器")


if __name__ == "__main__":
    main()
