# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## 环境

```bash
source .venv/bin/activate   # Python 3.14, pyvisa-py + libusb (无需 NI-VISA)
```

CLI 入口：
- `python -m instruments.cli awg|scope|dmm|lcr ...`（仪器控制）
- `python -m experiments.cli q-measure|q-sweep ...`（实验）

## 架构分层

```
instruments/          # 核心库：纯仪器驱动（可独立复用）
├── _backend.py       # USBTMC 通信 + 会话管理 (单例, 线程安全)
├── awg.py            # UTG962 信号发生器 (SCPI 写命令, 无查询)
├── scope.py          # SDS824X HD 示波器 (SCPI 读/写 + 波形换算)
├── dmm.py            # UT61E 万用表 (串口读数解析)
├── lcr.py            # UT612 LCR 电桥 (CP2110 HID 读数解析)
├── cli.py            # awg/scope CLI 入口
└── __init__.py

experiments/          # 应用层：电磁学实验（依赖 instruments）
├── q_measure.py      # 扫频法测 Q 值
├── cli.py            # 实验 CLI 入口
└── __init__.py
```

**关键设计**: `_backend.py` 为每台仪器维护独立 `ResourceManager`（隔离 UTG962 读缺陷对 SDS 的污染），全进程复用长期会话，反复 open/close 会导致 `Access denied`。

## 仪器

| Key | 设备 | 资源串 |
|-----|------|--------|
| `awg` | UNI-T UTG962 | `USB0::0x6656::0x0834::1021472514::INSTR` |
| `scope` | Siglent SDS824X HD | `USB0::0xF4EC::0x1017::SDS08A0C801504::INSTR` |
| `dmm` | UNI-T UT61E | 光电串口，默认读 `UT61E_PORT` |
| `lcr` | UNI-T UT612 | CP2110 HID USB-to-UART，VID/PID `10c4:ea80` |

### 必须遵守的约束

1. **AWG 只写不查** — 固件 USBTMC 读取有缺陷，第一条 query 后卡死。SCPI 写命令全部可靠。`awg.screenshot()` 是唯一读操作（裸 `:DISPlay?`），单独开大超时会话。
2. **示波器电压类 SCPI 测量超时** — `:MEASure:VPP?` 等会 `VI_ERROR_TMO`，幅度一律从 `get_waveform()` 波形数据自算。
3. **波形换算** — 从 `WAVeform:PREamble` 读取权威 `code_per_div/adc_bits`，公式 `V = int8(code) × (vdiv / code_per_div_8bit) - vdiv_offs`，不依赖硬编码常数。
4. **读波形会冻结采集** — `:WAVeform:DATA?` 使示波器进入 STOP；`get_waveform()` 已内建 `_ensure_live()` 自动恢复，调用者无需处理。
5. **PNG 截图分包** — SDS `:PRINt?` 返回的图像跨多个 USBTMC 包，`read_raw()` 在单包边界返回，必须循环读到 `IEND`。AWG `:DISPlay?` 同理，循环读到 `BM`+bmp_size。
6. **AWG 截图是 BMP 左右镜像** — `awg.screenshot()` 自动用 PIL `FLIP_LEFT_RIGHT` 翻正并转 PNG。
7. **UT61E 是串口慢速读数** — 使用 `19200 7O1`、`DTR=1`、`RTS=0`，适合电阻/直流/二极管/电容等基础读数，不用于高速采样。
8. **UT612 是 HID LCR 电桥** — 使用 Silicon Labs CP2110，配置 UART `9600 8N1` 后读取 17 字节帧；适合 L/C/R/ESR/Q/D/theta 标定，不是普通 `/dev/tty.*` 串口。

## 常用命令

```bash
# AWG: 只有写 (sine/square/off)
python -m instruments.cli awg sine --freq 1k --amp 2 --out
python -m instruments.cli awg off

# Scope
python -m instruments.cli scope stats -c 1          # 波形统计 (Vpp/Vrms/削顶检测)
python -m instruments.cli scope freq -c 1            # 频率测量
python -m instruments.cli scope screenshot --path out.png
python -m instruments.cli scope waveform -p w.csv -c 1

# DMM
export UT61E_PORT=/dev/tty.usbserial-xxxx
python -m instruments.cli dmm status
python -m instruments.cli dmm read

# LCR
python -m instruments.cli lcr status
python -m instruments.cli lcr read

# Q 值（自适应步长, 粗扫+细扫两阶段, ~30秒)
python -m experiments.cli q-measure --f-start 535k --f-stop 1605k \
    --amp 1 --vdiv 500m --load 50 --fine 50 -o output/q.png
```

数值支持 SI 后缀: `k`/`M`/`G` 用于频率, `m`/`u`/`n`/`p` 用于电压/电容/时间。

## Python 库直接调用

```python
from instruments import awg, scope
from experiments.q_measure import measure_q, plot_result

awg.configure(1, wave="sine", frequency=1e6, amplitude=2, load=10000, output=True)
st = scope.waveform_stats(1)  # {vpp, vrms, vmax, vmin, is_clipped, time[], voltage[], n_points}
img = scope.screenshot("PNG")  # bytes

result = measure_q(
    f_start=535e3, f_stop=1605e3,
    fine_points=50, amplitude_vpp=1, load_ohm=50, settle_s=0.2,
)
print(f"f0={result.f0/1e3:.1f} kHz, Q={result.q:.1f}")
```

详细操作说明见 `.agents/skills/instruments/SKILL.md`（含 Q 值接线图、故障排查、调制/扫频等高级功能）。
