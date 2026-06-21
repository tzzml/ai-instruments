# ai-instruments

通过 USB 远程控制 **UNI-T UTG962 信号发生器** + **Siglent SDS824X HD 示波器**，并测量线圈/谐振回路的 Q 值。

## 设备

| 设备 | 型号 | USB | 资源串 |
|------|------|-----|--------|
| 信号发生器 | UNI-T UTG962 (UTG900) | USBTMC `0x6656:0x0834` | `USB0::0x6656::0x0834::1021472514::INSTR` |
| 示波器 | Siglent SDS824X HD | USBTMC `0xF4EC:0x1017` | `USB0::0xF4EC::0x1017::SDS08A0C801504::INSTR` |

两台都是 **USBTMC**（USB488），用 pyvisa-py + libusb 控制，**无需 NI-VISA**。

## 安装

```bash
python3.14 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

依赖：pyvisa, pyvisa-py, pyusb, libusb, pyserial, numpy, matplotlib。

## 使用

激活环境后：

```bash
# 信号发生器
python -m instruments.cli awg sine --freq 1k --amp 2 --out   # 1kHz 2Vpp 正弦
python -m instruments.cli awg off                              # 关输出

# 示波器
python -m instruments.cli scope setup -c 1 --vdiv 500m --tdiv 1m --coupling DC
python -m instruments.cli scope stats -c 1                     # Vpp/Vrms/...
python -m instruments.cli scope screenshot --path out.png      # 截图
python -m instruments.cli scope waveform --path wave.csv -c 1  # 波形 CSV

# Q 值测量
python -m experiments.cli q-measure --f-start 535k --f-stop 1605k \
    --amp 1 --vdiv 500m --load 50 --fine 50 -o output/q.png
```

数值支持 SI 后缀（`k`/`M`/`G`/`m`/`u`/`n`/`p`）。

## 作为 Python 库

```python
from instruments import awg, scope
from experiments.q_measure import measure_q

awg.configure(1, wave="sine", frequency=1e6, amplitude=2, load=10000, output=True)
stats = scope.waveform_stats(1)   # {vpp, vrms, vmax, vmin, time, voltage, n_points}
img = scope.screenshot("PNG")     # bytes

result = measure_q(f_start=535e3, f_stop=1605e3,
                   fine_points=50, amplitude_vpp=1, load_ohm=50)
print(f"f0 = {result.f0/1e3:.1f} kHz, Q = {result.q:.1f}")
```

## 已验证的关键参数（2026-06-20）

- 波形换算：从 `WAVeform:PREamble` 读取 `code_per_div/adc_bits`，按 `V = int8(code) × (vdiv / code_per_div_8bit) - offset` 换算；2Vpp 真值标定误差 <1%
- 示波器 FREQ 测量可靠；VPP/VRMS 等 SCPI 查询超时（固件怪癖），幅度从波形自算
- UTG962 写命令全部可靠，但 USBTMC 读取不稳定；业务路径只写不查，Web 状态页也不查询 AWG
- SDS 用新版 SCPI 树（`:CHANnel1:SCALe`），非老式 LeCroy（`C1:VDIV`）

详见 `.agents/skills/instruments/SKILL.md`。
