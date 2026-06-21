"""
ai-instruments 命令行入口。

用法:
  python -m instruments.cli awg sine --freq 1000 --amp 2 --out
  python -m instruments.cli awg off
  python -m instruments.cli scope stats
  python -m instruments.cli scope screenshot out.png
  python -m instruments.cli scope waveform out.csv
  python -m instruments.cli q-measure --f-start 100k --f-stop 3M --cap 100p -o q.png

频率/电容支持 SI 后缀: k/M/G, p/n/u/m。
"""
from __future__ import annotations

import argparse
import csv
import sys
from typing import Optional


def _parse_value(s: str) -> float:
    """解析带 SI 后缀的数值: 1k, 2.5M, 100p, 1u, 3.3m ..."""
    s = s.strip()
    if not s:
        raise ValueError("空值")
    suffix_map = {
        "G": 1e9, "M": 1e6, "K": 1e3, "k": 1e3,
        "m": 1e-3, "u": 1e-6, "U": 1e-6, "n": 1e-9, "N": 1e-9,
        "p": 1e-12, "P": 1e-12, "f": 1e-15,
    }
    last = s[-1]
    if last in suffix_map:
        return float(s[:-1]) * suffix_map[last]
    return float(s)


def _fmt_hz(hz: float) -> str:
    if hz >= 1e6:
        return "%.4f MHz" % (hz / 1e6)
    if hz >= 1e3:
        return "%.4f kHz" % (hz / 1e3)
    return "%.4f Hz" % hz


def _fmt_v(v: float) -> str:
    if abs(v) >= 1:
        return "%.4f V" % v
    if abs(v) >= 1e-3:
        return "%.4f mV" % (v * 1e3)
    return "%.4f µV" % (v * 1e6)


def cmd_awg(args):
    from . import awg
    if args.action == "sine":
        awg.configure(1, wave="sine",
                      frequency=_parse_value(args.freq) if args.freq else 1000,
                      amplitude=args.amp, amplitude_unit="vpp",
                      offset=args.offset or 0, load=args.load or 10000,
                      output=args.out)
        print("AWG CH1: sine %s, %.2f Vpp, offset %sV, load %sΩ, 输出 %s" % (
            _fmt_hz(_parse_value(args.freq)) if args.freq else "1 kHz",
            args.amp, args.offset or 0, args.load or 10000, "开" if args.out else "关"))
    elif args.action == "square":
        awg.configure(1, wave="square",
                      frequency=_parse_value(args.freq) if args.freq else 1000,
                      amplitude=args.amp, duty=args.duty or 50,
                      load=args.load or 10000, output=args.out)
        print("AWG CH1: square %s, %.2f Vpp, duty %s%%, 输出 %s" % (
            _fmt_hz(_parse_value(args.freq)) if args.freq else "1 kHz",
            args.amp, args.duty or 50, "开" if args.out else "关"))
    elif args.action == "off":
        awg.output_off(1)
        print("AWG CH1 输出已关闭")


def cmd_scope(args):
    from . import scope
    if args.action == "stats":
        st = scope.waveform_stats(args.channel)
        print("通道 C%d:" % args.channel)
        print("  采样点: %d" % st["n_points"])
        print("  时间窗: %.3f ms" % ((st["time"][-1] - st["time"][0]) * 1e3))
        print("  Vpp : %s" % _fmt_v(st["vpp"]))
        print("  Vrms: %s" % _fmt_v(st["vrms"]))
        print("  Vmax: %s  Vmin: %s" % (_fmt_v(st["vmax"]), _fmt_v(st["vmin"])))
    elif args.action == "freq":
        print("C%d FREQ = %s" % (args.channel, _fmt_hz(scope.measure_freq(args.channel))))
    elif args.action == "screenshot":
        data = scope.screenshot("PNG")
        with open(args.path, "wb") as f:
            f.write(data)
        print("截图已保存: %s (%d bytes)" % (args.path, len(data)))
    elif args.action == "waveform":
        t, v = scope.get_waveform(args.channel)
        with open(args.path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["time_s", "voltage_v"])
            for ti, vi in zip(t, v):
                w.writerow(["%.9e" % ti, "%.6f" % vi])
        print("波形已保存: %s (%d 点)" % (args.path, len(v)))
    elif args.action == "setup":
        scope.channel_on(args.channel, True)
        if args.vdiv:
            scope.set_scale(args.channel, _parse_value(args.vdiv))
        if args.tdiv:
            scope.set_timebase(_parse_value(args.tdiv))
        if args.coupling:
            scope.set_coupling(args.channel, args.coupling)
        print("示波器 C%d 已配置" % args.channel)
    elif args.action == "idn":
        print(scope.identity())


def main(argv=None):
    global _InstrumentError
    from ._backend import InstrumentError as _InstrumentError
    parser = argparse.ArgumentParser(prog="instruments", description="UTG962 + SDS824X HD 控制")
    sub = parser.add_subparsers(dest="dev", required=True)

    # AWG
    p_awg = sub.add_parser("awg", help="信号发生器")
    p_awg.add_argument("action", choices=["sine", "square", "off"])
    p_awg.add_argument("--freq", "-f", help="频率 (如 1k, 2.5M)")
    p_awg.add_argument("--amp", type=float, default=2.0, help="幅度 Vpp (高阻)")
    p_awg.add_argument("--offset", type=float, help="直流偏置 V")
    p_awg.add_argument("--duty", type=float, help="占空比 %% (方波)")
    p_awg.add_argument("--load", type=float, help="负载电阻 Ω")
    p_awg.add_argument("--out", action="store_true", help="打开输出")
    p_awg.set_defaults(func=cmd_awg)

    # Scope
    p_sc = sub.add_parser("scope", help="示波器")
    p_sc.add_argument("action", choices=["stats", "freq", "screenshot", "waveform", "setup", "idn"])
    p_sc.add_argument("--channel", "-c", type=int, default=1)
    p_sc.add_argument("--path", help="保存路径 (screenshot/waveform)")
    p_sc.add_argument("--vdiv", help="垂直档位 V/div (如 0.5, 100m)")
    p_sc.add_argument("--tdiv", help="时基 s/div (如 1m, 500u)")
    p_sc.add_argument("--coupling", choices=["AC", "DC"])
    p_sc.set_defaults(func=cmd_scope)

    args = parser.parse_args(argv)
    try:
        args.func(args)
    except _InstrumentError as e:
        print("错误: %s" % e, file=sys.stderr)
        sys.exit(1)



if __name__ == "__main__":
    main()
