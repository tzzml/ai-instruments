"""
ai-instruments 实验 CLI，基于 instruments 核心库。

用法:
  python -m experiments.cli q-measure --f-start 535k --f-stop 1605k --fine 50 -o output/q.png
  python -m experiments.cli q-sweep --f-start 840k --f-stop 880k --sweep-time 2
"""
from __future__ import annotations

import argparse
import sys


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


def cmd_q_measure(args):
    from . import q_measure

    def progress(done, tot, freq):
        print("\r[%d/%d] 扫频 %s ..." % (done, tot, _fmt_hz(freq)), end="", flush=True)

    print("开始 Q 值扫频测量 (%s ~ %s, 粗%d+细%d点)..." % (
        _fmt_hz(_parse_value(args.f_start)), _fmt_hz(_parse_value(args.f_stop)),
        args.coarse if not args.coarse_step else "auto", args.fine))
    result = q_measure.measure_q(
        f_start=_parse_value(args.f_start),
        f_stop=_parse_value(args.f_stop),
        coarse_points=args.coarse,
        coarse_step_hz=_parse_value(args.coarse_step) if args.coarse_step else None,
        q_min=args.q_min,
        fine_points=args.fine,
        amplitude_vpp=args.amp,
        scope_channel=args.channel,
        scope_vdiv=_parse_value(args.vdiv) if args.vdiv else None,
        capacitance_f=_parse_value(args.cap) if args.cap else None,
        load_ohm=args.load,
        settle_s=args.settle,
        progress=progress,
    )
    print("\n\n=== Q 值测量结果 ===")
    print("  谐振频率 f0 = %s" % _fmt_hz(result.f0))
    print("  谐振幅度   = %s" % _fmt_v(result.peak_vrms))
    print("  -3dB 频率  = %s ~ %s" % (_fmt_hz(result.f1), _fmt_hz(result.f2)))
    print("  带宽 BW    = %s" % _fmt_hz(result.bandwidth))
    print("  ★ Q 值     = %.1f" % result.q)
    if result.inductance_h is not None:
        print("  电感量 L   ≈ %.3f µH (基于 C=%s)" % (
            result.inductance_h * 1e6, args.cap))

    if args.output:
        from .q_measure import plot_result
        plot_result(result, save_path=args.output)
        print("\n图表已保存: %s" % args.output)


def cmd_q_sweep(args):
    from . import q_measure

    print("开始 AWG 扫频 Q 值测量 (%s ~ %s, %.1f 秒)..." % (
        _fmt_hz(_parse_value(args.f_start)), _fmt_hz(_parse_value(args.f_stop)),
        args.sweep_time))
    result = q_measure.measure_q_sweep(
        f_start=_parse_value(args.f_start),
        f_stop=_parse_value(args.f_stop),
        sweep_time_s=args.sweep_time,
        amplitude_vpp=args.amp,
        scope_channel=args.channel,
        scope_vdiv=_parse_value(args.vdiv) if args.vdiv else 0.5,
        capacitance_f=_parse_value(args.cap) if args.cap else None,
        load_ohm=args.load,
    )
    print("\n=== Q 值测量结果 ===")
    print("  谐振频率 f0 = %s" % _fmt_hz(result.f0))
    print("  谐振幅度   = %s" % _fmt_v(result.peak_vrms))
    print("  -3dB 频率  = %s ~ %s" % (_fmt_hz(result.f1), _fmt_hz(result.f2)))
    print("  带宽 BW    = %s" % _fmt_hz(result.bandwidth))
    print("  ★ Q 值     = %.1f" % result.q)
    if result.inductance_h is not None:
        print("  电感量 L   ≈ %.3f µH (基于 C=%s)" % (
            result.inductance_h * 1e6, args.cap))

    if args.output:
        from .q_measure import plot_result
        plot_result(result, save_path=args.output)
        print("\n图表已保存: %s" % args.output)


def main(argv=None):
    from instruments._backend import InstrumentError

    parser = argparse.ArgumentParser(prog="experiments", description="电磁学实验 (基于 instruments 核心库)")
    sub = parser.add_subparsers(dest="exp", required=True)

    # Q measure
    p_q = sub.add_parser("q-measure", help="线圈 Q 值逐点扫频测量")
    p_q.add_argument("--f-start", default="100k", help="起始频率 (默认 100k)")
    p_q.add_argument("--f-stop", default="3M", help="终止频率 (默认 3M)")
    p_q.add_argument("--coarse", type=int, default=50, help="粗扫点数 (对数间隔)")
    p_q.add_argument("--coarse-step", help="粗扫固定步长 Hz (如 10k), 设了则忽略 coarse/q-min")
    p_q.add_argument("--q-min", type=float, default=30, help="预期最小 Q 值, 自动算粗扫步长")
    p_q.add_argument("--fine", type=int, default=60, help="细扫点数")
    p_q.add_argument("--amp", type=float, default=2.0, help="AWG 幅度 Vpp")
    p_q.add_argument("--channel", "-c", type=int, default=1)
    p_q.add_argument("--vdiv", help="示波器档位 V/div (留空自动)")
    p_q.add_argument("--cap", help="谐振电容值 (如 100p), 给定则反推电感")
    p_q.add_argument("--load", type=float, default=10000, help="AWG 负载电阻 Ω (50 或 10000)")
    p_q.add_argument("--settle", type=float, default=0.3, help="每步稳定时间 s (默认 0.3)")
    p_q.add_argument("--output", "-o", help="输出图表路径 (PNG)")
    p_q.set_defaults(func=cmd_q_measure)

    # Q sweep (实验性)
    p_qs = sub.add_parser("q-sweep", help="线圈 Q 值 AWG 扫频测量 [实验性]")
    p_qs.add_argument("--f-start", default="535k", help="起始频率")
    p_qs.add_argument("--f-stop", default="1605k", help="终止频率")
    p_qs.add_argument("--sweep-time", type=float, default=2.0, help="扫频时长 s")
    p_qs.add_argument("--amp", type=float, default=1.0, help="AWG 幅度 Vpp")
    p_qs.add_argument("--channel", "-c", type=int, default=1)
    p_qs.add_argument("--vdiv", help="示波器档位 V/div")
    p_qs.add_argument("--cap", help="谐振电容值 (如 100p)")
    p_qs.add_argument("--load", type=float, default=50, help="AWG 负载电阻 Ω")
    p_qs.add_argument("--output", "-o", help="输出图表路径 (PNG)")
    p_qs.set_defaults(func=cmd_q_sweep)

    args = parser.parse_args(argv)
    try:
        args.func(args)
    except InstrumentError as e:
        print("错误: %s" % e, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
