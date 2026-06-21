from __future__ import annotations

import json
import unittest
from unittest import mock
from pathlib import Path

import numpy as np


def _workbench_assets_text():
    base = Path("experiments/static")
    return "\n".join(
        (base / name).read_text(encoding="utf-8")
        for name in ("index.html", "app.css", "workbench.js")
    )


class ExperimentProfilesTest(unittest.TestCase):
    def test_profiles_include_available_materials_and_safety(self):
        from experiments.profiles import EXPERIMENT_PROFILES

        keys = {p["key"] for p in EXPERIMENT_PROFILES}
        self.assertIn("mineral_loop", keys)
        self.assertIn("spiderweb_loop", keys)
        self.assertIn("telescopic_dipole_pair", keys)
        self.assertIn("nested_coils_core", keys)
        self.assertIn("avalanche_pulser", keys)
        self.assertIn("awg_square_tdr_source", keys)
        self.assertIn("diode_detector_parts", keys)
        self.assertIn("passive_impedance_parts", keys)

        nested = next(p for p in EXPERIMENT_PROFILES if p["key"] == "nested_coils_core")
        self.assertIn("原副边教学线圈", nested["name"])
        self.assertNotIn("大小套合", nested["name"])
        self.assertIn("可插拔铁芯", nested["summary"])
        self.assertTrue(nested["safety"])

        diode = next(p for p in EXPERIMENT_PROFILES if p["key"] == "diode_detector_parts")
        self.assertIn("二极管", diode["name"])
        self.assertIn("检波", diode["summary"])
        self.assertTrue(diode["safety"])

        mineral = next(p for p in EXPERIMENT_PROFILES if p["key"] == "mineral_loop")
        spiderweb = next(p for p in EXPERIMENT_PROFILES if p["key"] == "spiderweb_loop")
        self.assertIn("22.5 cm", mineral["name"] + mineral["summary"])
        self.assertIn("28 匝", mineral["summary"])
        self.assertIn("0.8 mm", mineral["summary"])
        self.assertIn("无氧铜漆包线", mineral["summary"])
        self.assertIn("80 cm", spiderweb["name"] + spiderweb["summary"])
        self.assertIn("多股", spiderweb["summary"])
        self.assertIn("利兹线", spiderweb["summary"])

        passive = next(p for p in EXPERIMENT_PROFILES if p["key"] == "passive_impedance_parts")
        self.assertIn("电感", passive["summary"])
        self.assertIn("电容", passive["summary"])
        self.assertIn("采样电阻", passive["summary"])

        dipole = next(p for p in EXPERIMENT_PROFILES if p["key"] == "telescopic_dipole_pair")
        self.assertIn("1.25 m", dipole["summary"])
        self.assertEqual(dipole["recommended"]["arm_length_m"], 1.25)
        self.assertEqual(dipole["recommended"]["frequency_start_hz"], 40e6)
        self.assertEqual(dipole["recommended"]["frequency_stop_hz"], 60e6)

    def test_experiment_stations_are_independent_and_bind_materials(self):
        from experiments.profiles import EXPERIMENT_STATIONS

        self.assertGreater(len(EXPERIMENT_STATIONS), 5)
        station_keys = {s["key"] for s in EXPERIMENT_STATIONS}
        self.assertIn("mw_q_sweep", station_keys)
        self.assertIn("pulse_tdr_length", station_keys)
        self.assertIn("diode_va_curve", station_keys)

        for station in EXPERIMENT_STATIONS:
            self.assertTrue(station["parent_key"])
            self.assertTrue(station["goal"])
            self.assertTrue(station["circuit"])
            self.assertTrue(station["acquisition"])
            self.assertTrue(station["controls"])
            self.assertTrue(station["materials"])
            self.assertTrue(station["parameters"])

        mw = next(s for s in EXPERIMENT_STATIONS if s["key"] == "mw_standard_field")
        self.assertIn("standard_mw_test_loop", mw["materials"])
        self.assertIn("一米", "".join(mw["circuit"]))

    def test_course_modules_follow_learning_path_order(self):
        from experiments.profiles import COURSE_MODULES

        self.assertEqual(
            [
                "rc_basics",
                "passive_impedance",
                "diode_detector",
                "mw_resonance",
                "coils_core",
                "pulse_tdr",
                "near_field_antennas",
            ],
            [m["key"] for m in COURSE_MODULES],
        )

    def test_diode_detector_topic_is_split_into_leaf_experiments(self):
        from experiments.profiles import COURSE_MODULES, EXPERIMENT_STATIONS

        modules = {m["key"]: m for m in COURSE_MODULES}
        self.assertIn("diode_detector", modules)
        self.assertIn("包络", modules["diode_detector"]["goal"])

        diode_children = [s for s in EXPERIMENT_STATIONS if s["parent_key"] == "diode_detector"]
        self.assertEqual(
            {"diode_va_curve", "diode_half_wave_rectifier", "diode_am_detector"},
            {s["key"] for s in diode_children},
        )

        by_key = {s["key"]: s for s in diode_children}
        self.assertEqual("/api/exp/diode-va", by_key["diode_va_curve"]["api"])
        self.assertEqual("ramp", by_key["diode_va_curve"]["acquisition"]["awg"]["wave"])
        self.assertEqual([1, 2], by_key["diode_va_curve"]["acquisition"]["scope"]["channels"])
        self.assertIn("diode_detector_parts", by_key["diode_am_detector"]["materials"])
        self.assertIn("AM", "".join(by_key["diode_am_detector"]["circuit"]))

    def test_passive_impedance_topic_has_capacitor_inductor_and_rlc_experiments(self):
        from experiments.profiles import COURSE_MODULES, EXPERIMENT_STATIONS

        modules = {m["key"]: m for m in COURSE_MODULES}
        self.assertIn("passive_impedance", modules)
        self.assertIn("阻抗", modules["passive_impedance"]["goal"])

        children = [s for s in EXPERIMENT_STATIONS if s["parent_key"] == "passive_impedance"]
        self.assertEqual(
            {"capacitor_impedance", "inductor_impedance", "rlc_impedance_phase"},
            {s["key"] for s in children},
        )
        by_key = {s["key"]: s for s in children}
        self.assertEqual("/api/exp/impedance-point", by_key["capacitor_impedance"]["api"])
        self.assertEqual("impedance_point", by_key["inductor_impedance"]["experiment"])
        for station in children:
            self.assertIn("passive_impedance_parts", station["materials"])
            self.assertIn("rsense_ohm", {p["id"] for p in station["parameters"]})
            self.assertEqual(station["acquisition"]["scope"]["channels"], [1, 2])

    def test_middle_wave_topic_is_split_into_leaf_experiments(self):
        from experiments.profiles import EXPERIMENT_STATIONS

        mw_children = [s for s in EXPERIMENT_STATIONS if s["parent_key"] == "mw_resonance"]
        self.assertGreaterEqual(len(mw_children), 4)
        self.assertEqual(
            {"mw_q_sweep", "mw_ringdown", "mw_cap_tuning", "mw_standard_field"},
            {s["key"] for s in mw_children},
        )
        for child in mw_children:
            self.assertNotIn("/", child["title"])
            self.assertEqual(child["experiment"], child["parameters"][0].get("experiment", child["experiment"]))
        q_sweep = next(s for s in mw_children if s["key"] == "mw_q_sweep")
        q_text = q_sweep["goal"] + "".join(q_sweep["circuit"])
        self.assertIn("22.5 cm", q_text)
        self.assertIn("80 cm", q_text)
        self.assertIn("无氧铜", q_text)
        self.assertIn("利兹线", q_text)
        standard = next(s for s in mw_children if s["key"] == "mw_standard_field")
        standard_text = standard["goal"] + "".join(standard["circuit"]) + "".join(standard["acquisition"]["measure"])
        self.assertIn("同一标准中波场强", standard_text)
        self.assertIn("感应电压", standard_text)
        self.assertIn("弱台接收能力", standard_text)
        self.assertIn("电压增益比", standard_text)
        self.assertIn("rx_antenna", {p["id"] for p in standard["parameters"]})
        self.assertIn("load_ohm", {p["id"] for p in standard["parameters"]})
        for child in mw_children:
            self.assertEqual("/assets/mw-loop-field-comparison.png", child["image"]["src"])

    def test_experiment_parameter_sets_are_station_specific(self):
        from experiments.profiles import EXPERIMENT_STATIONS

        by_key = {s["key"]: {p["id"] for p in s["parameters"]} for s in EXPERIMENT_STATIONS}

        self.assertIn("resistance_ohm", by_key["rc_filter_phase"])
        self.assertIn("capacitance_f", by_key["rc_filter_phase"])
        self.assertNotIn("velocity_factor", by_key["rc_filter_phase"])

        self.assertIn("core", by_key["coil_coupling"])
        self.assertIn("load_ohm", by_key["coil_transformer"])
        self.assertNotIn("core", by_key["antenna_distance"])

        self.assertIn("termination", by_key["pulse_tdr_length"])
        self.assertIn("velocity_factor", by_key["pulse_tdr_length"])
        self.assertNotEqual(by_key["mw_q_sweep"], by_key["pulse_tdr_length"])

    def test_dipole_topic_targets_resonant_rf_field_experiments(self):
        from experiments.profiles import COURSE_MODULES, EXPERIMENT_STATIONS

        module = next(m for m in COURSE_MODULES if m["key"] == "near_field_antennas")
        self.assertIn("60 MHz", module["goal"])

        children = [s for s in EXPERIMENT_STATIONS if s["parent_key"] == "near_field_antennas"]
        self.assertEqual(
            {
                "antenna_resonance_sweep",
                "antenna_length_tuning",
                "antenna_distance",
                "antenna_polarization",
            },
            {s["key"] for s in children},
        )

        by_key = {s["key"]: s for s in children}
        sweep = by_key["antenna_resonance_sweep"]
        self.assertEqual("/api/exp/q-sweep", sweep["api"])
        self.assertEqual("q_sweep", sweep["experiment"])
        self.assertIn("40M", {p["default"] for p in sweep["parameters"]})
        self.assertIn("60M", {p["default"] for p in sweep["parameters"]})
        self.assertIn("arm_length_m", {p["id"] for p in sweep["parameters"]})
        self.assertEqual(sweep["acquisition"]["scope"]["channels"], [1, 2])
        self.assertIn("半波", sweep["goal"])

        length = by_key["antenna_length_tuning"]
        self.assertIn("arm_length_m", {p["id"] for p in length["parameters"]})
        self.assertIn("谐振频率", "".join(length["circuit"]) + length["goal"])
        self.assertIn("relative_db", {p["id"] for p in by_key["antenna_distance"]["parameters"]})

    def test_tdr_defaults_to_awg_square_edge_source(self):
        from experiments.profiles import EXPERIMENT_STATIONS

        tdr = next(s for s in EXPERIMENT_STATIONS if s["key"] == "pulse_tdr_length")
        self.assertIn("awg_square_tdr_source", tdr["materials"])
        self.assertEqual(tdr["acquisition"]["awg"]["wave"], "square")
        self.assertIn("慢边沿", tdr["goal"])
        tdr_text = tdr["goal"] + "".join(tdr["circuit"]) + "".join(p["label"] for p in tdr["parameters"])
        self.assertIn("重复频率", tdr_text)
        self.assertIn("上升沿", tdr_text)
        self.assertIn("反射不重叠", tdr_text)
        self.assertNotEqual(tdr["acquisition"]["awg"].get("source"), "avalanche_pulser")

    def test_pulse_topic_includes_four_point_propagation_experiment(self):
        from experiments.profiles import EXPERIMENT_STATIONS

        station = next(s for s in EXPERIMENT_STATIONS if s["key"] == "pulse_propagation_4ch")
        self.assertEqual(station["parent_key"], "pulse_tdr")
        self.assertEqual(station["experiment"], "propagation_4ch")
        self.assertEqual(station["api"], "/api/exp/propagation-4ch")
        self.assertEqual(station["acquisition"]["scope"]["channels"], [1, 2, 3, 4])
        self.assertIn("0/10/20/30 m", station["goal"])
        self.assertIn("tap_distances_m", {p["id"] for p in station["parameters"]})
        self.assertEqual("/assets/twisted-pair-tdr.png", station["image"]["src"])

    def test_static_workbench_keeps_materials_in_experiment_detail_not_sidebar(self):
        html = Path("experiments/static/index.html").read_text(encoding="utf-8")

        self.assertNotIn("本实验材料</h2>", html)
        self.assertIn('id="experimentMaterials"', html)
        self.assertIn('id="moduleNav"', html)
        self.assertNotIn('id="moduleNav" class="childNav"', html)

    def test_static_workbench_collapses_inactive_modules(self):
        html = _workbench_assets_text()

        self.assertIn("activeModuleKey", html)
        self.assertIn("toggleModule", html)
        self.assertIn("module-chevron", html)
        self.assertNotIn("module-count", html)
        self.assertNotIn("m.goal+'</small>'", html)
        self.assertNotIn(".childNav{position:relative", html)
        self.assertNotIn(".childNav button.active{background:#f7fbfc;border", html)
        self.assertIn("group.style.display=currentStation&&currentStation.parent_key===m.key?'block':'none'", html)

    def test_static_workbench_has_dedicated_instrument_setup_panel(self):
        html = _workbench_assets_text()

        self.assertIn('id="instrumentSetup"', html)
        self.assertIn('id="awgSetup"', html)
        self.assertIn('id="scopeSetup"', html)
        self.assertIn("applyInstrumentSetup", html)
        self.assertIn("scopeAutoset", html)
        self.assertIn("captureInstrumentScreens", html)
        self.assertIn("/api/awg/screenshot", html)
        self.assertIn("/api/scope/autoset", html)

    def test_static_workbench_renders_experiment_images(self):
        from experiments.profiles import EXPERIMENT_STATIONS

        html = _workbench_assets_text()

        self.assertIn('id="experimentImage"', html)
        self.assertIn('id="imageModal"', html)
        self.assertIn('id="imageModalImg"', html)
        self.assertIn("renderExperimentImage", html)
        self.assertIn("openImageModal", html)
        self.assertIn("closeImageModal", html)
        self.assertIn("点击放大", html)
        self.assertIn("currentStation.image", html)
        for asset in [
            "mw-loop-field-comparison.png",
            "dipole-rx-tx.png",
            "primary-secondary-coils.png",
            "twisted-pair-tdr.png",
            "diode-iv-switch-comparison.png",
        ]:
            self.assertTrue(Path("experiments/static/assets", asset).exists())
        imaged = [s for s in EXPERIMENT_STATIONS if "image" in s]
        self.assertGreaterEqual(len(imaged), 10)
        for station in imaged:
            self.assertTrue(station["image"]["src"].startswith("/assets/"))
            self.assertTrue(station["image"]["alt"])

    def test_static_workbench_uses_external_assets(self):
        html = Path("experiments/static/index.html").read_text(encoding="utf-8")

        self.assertIn('href="/app.css"', html)
        self.assertIn('src="/workbench.js"', html)
        self.assertNotIn("<style>", html)
        self.assertNotIn("<script>\nvar modules", html)
        self.assertTrue(Path("experiments/static/app.css").exists())
        self.assertTrue(Path("experiments/static/workbench.js").exists())

    def test_static_workbench_separates_instrument_apply_from_screen_capture(self):
        html = _workbench_assets_text()

        self.assertIn("抓取仪器屏幕", html)
        apply_start = html.index("function applyInstrumentSetup")
        capture_start = html.index("function captureInstrumentScreens")
        apply_function = html[apply_start:capture_start]
        self.assertNotIn("captureInstrumentScreens()", apply_function)

    def test_static_workbench_normalizes_instrument_screen_sizes(self):
        html = _workbench_assets_text()

        self.assertIn('class="screen-frame hint" id="awgScreen"', html)
        self.assertIn('class="screen-frame hint" id="scopeScreen"', html)
        self.assertIn(".screen-frame{", html)
        self.assertIn("aspect-ratio:16/9", html)
        self.assertIn("object-fit:contain", html)

    def test_static_workbench_renders_diode_va_results(self):
        html = _workbench_assets_text()

        self.assertIn("diode_va", html)
        self.assertIn("/api/exp/diode-va", html)
        self.assertIn("drawDiodeVA", html)
        self.assertIn("二极管伏安", html)

    def test_diode_va_experiment_supports_detector_diode_switching(self):
        from experiments.profiles import EXPERIMENT_STATIONS

        station = next(s for s in EXPERIMENT_STATIONS if s["key"] == "diode_va_curve")
        text = station["goal"] + "".join(station["circuit"]) + "".join(station["controls"])
        self.assertEqual("/assets/diode-iv-switch-comparison.png", station["image"]["src"])
        self.assertIn("多路切换", text)
        self.assertIn("I-V 曲线", text)
        diode_param = next(p for p in station["parameters"] if p["id"] == "diode_type")
        option_values = {value for value, _label in diode_param["options"]}
        self.assertEqual({"2ap9", "1n34", "1n60", "1ss86", "1ss106", "bat85", "other"}, option_values)

    def test_static_workbench_renders_impedance_experiments(self):
        html = _workbench_assets_text()

        self.assertIn("impedance_point", html)
        self.assertIn("/api/exp/impedance-point", html)
        self.assertIn("阻抗幅值", html)
        self.assertIn("R_sense", html)

    def test_static_workbench_renders_four_channel_propagation(self):
        html = _workbench_assets_text()

        self.assertIn("propagation_4ch", html)
        self.assertIn("/api/exp/propagation-4ch", html)
        self.assertIn("传播速度", html)
        self.assertIn("0/10/20/30m", html)

    def test_static_workbench_renders_rf_dipole_schematics(self):
        html = _workbench_assets_text()

        self.assertIn("antenna_resonance_sweep", html)
        self.assertIn("半波谐振", html)
        self.assertIn("长度调谐", html)
        self.assertIn("场强/dB", html)

    def test_static_workbench_moves_full_records_to_separate_page(self):
        html = Path("experiments/static/index.html").read_text(encoding="utf-8")
        records = Path("experiments/static/records.html")

        self.assertNotIn('<section class="band records">', html)
        self.assertIn("/records.html", html)
        self.assertTrue(records.exists())

    def test_panel_controls_multiple_channels_and_screenshots(self):
        html = Path("experiments/static/panel.html").read_text(encoding="utf-8")

        self.assertIn('id="awgChannels"', html)
        self.assertIn('id="scopeChannels"', html)
        self.assertIn('id="dmmReadout"', html)
        self.assertIn("/api/dmm/read", html)
        self.assertIn("refreshDmmStatus", html)
        self.assertIn('id="lcrReadout"', html)
        self.assertIn("/api/lcr/read", html)
        self.assertIn("refreshLcrStatus", html)
        self.assertIn("万用表 UT61E", html)
        self.assertIn("LCR 电桥 UT612", html)
        self.assertNotIn("低速测量仪表", html)
        self.assertNotIn("meter-grid", html)
        self.assertIn("renderAwgChannels", html)
        self.assertIn("renderScopeChannels", html)
        self.assertIn("/api/panel/status", html)
        self.assertIn("/api/scope/autoset", html)
        self.assertIn("scopeAutoset", html)
        self.assertIn("/api/awg/screenshot", html)
        self.assertIn("/api/scope/screenshot", html)
        self.assertIn("CH1", html)
        self.assertIn("CH4", html)


class DmmTest(unittest.TestCase):
    def test_ut61e_parser_converts_frame_to_normalized_reading(self):
        from instruments import dmm

        # Range 1 for voltage means 2.2000 V full scale. Digits 12345 -> 1.2345 V.
        frame = bytes([0x31, 0x31, 0x32, 0x33, 0x34, 0x35, 0x3B, 0x30, 0x30, 0x30, 0x3A, 0x30, 0x0D, 0x0A])
        reading = dmm.parse_frame(frame, timestamp=123.0)

        self.assertTrue(reading.data_valid)
        self.assertAlmostEqual(reading.value, 1.2345)
        self.assertEqual(reading.units, "V")
        self.assertEqual(reading.display, "1.2345 V")
        self.assertEqual(reading.mode, "V")
        self.assertTrue(reading.dc)
        self.assertTrue(reading.auto)
        self.assertFalse(reading.ac)
        self.assertEqual(reading.timestamp, 123.0)

    def test_ut61e_status_reports_configured_port_without_opening_serial(self):
        from instruments import dmm

        with mock.patch.dict("os.environ", {"UT61E_PORT": "/dev/tty.usbserial-test"}), \
             mock.patch.object(dmm, "candidate_ports", return_value=["/dev/tty.usbserial-test"]):
            st = dmm.status()

        self.assertTrue(st["configured"])
        self.assertEqual(st["port"], "/dev/tty.usbserial-test")
        self.assertIn("/dev/tty.usbserial-test", st["candidates"])


class LcrTest(unittest.TestCase):
    def test_ut612_parser_decodes_reference_resistance_theta_frame(self):
        from instruments import lcr

        frame = bytes([
            0x00, 0x0D, 0x60, 0x58, 0x00, 0x03, 0x13, 0x90, 0x14,
            0x00, 0x04, 0x00, 0x00, 0x71, 0x80, 0x0D, 0x0A,
        ])
        reading = lcr.decode_frame(frame, timestamp=123.0)

        self.assertEqual(reading.main_mode, "Rs")
        self.assertEqual(reading.frequency, "1KHz")
        self.assertEqual(reading.frequency_hz, 1000.0)
        self.assertEqual(reading.primary.text, "0.5008 kOhm")
        self.assertAlmostEqual(reading.primary.value, 0.5008)
        self.assertEqual(reading.secondary_mode, "theta")
        self.assertEqual(reading.secondary.text, "0.0 Deg")
        self.assertTrue(reading.flags["auto"])
        self.assertFalse(reading.flags["parallel"])
        self.assertEqual(reading.timestamp, 123.0)

    def test_ut612_parser_decodes_parallel_capacitance_and_ol_secondary(self):
        from instruments import lcr

        frame = bytes([
            0x00, 0x0D, 0x80, 0x58, 0x00, 0x02, 0x01, 0x33, 0x49,
            0x00, 0x01, 0x4E, 0x20, 0x01, 0xC3, 0x0D, 0x0A,
        ])
        reading = lcr.decode_frame(frame)

        self.assertEqual(reading.main_mode, "Cp")
        self.assertEqual(reading.primary.text, "30.7 pF")
        self.assertEqual(reading.secondary_mode, "D")
        self.assertEqual(reading.secondary.text, "OL")
        self.assertTrue(reading.flags["parallel"])

    def test_ut612_frame_drain_resynchronizes_from_noise(self):
        from instruments import lcr

        frame = bytes([
            0x00, 0x0D, 0x40, 0x58, 0x00, 0x01, 0x00, 0x64, 0x29,
            0x00, 0x02, 0x00, 0x01, 0x04, 0x80, 0x0D, 0x0A,
        ])
        buf = bytearray(b"noise" + frame)
        frames = lcr.drain_frames(buf)

        self.assertEqual(frames, [frame])
        self.assertEqual(buf, bytearray())


class QBoundaryTest(unittest.TestCase):
    def test_q_from_sweep_requires_both_3db_crossings(self):
        from experiments.analysis import q_from_sweep

        complete = q_from_sweep(
            [
                (900.0, 0.2),
                (950.0, 0.75),
                (1000.0, 1.0),
                (1050.0, 0.75),
                (1100.0, 0.2),
            ]
        )
        self.assertTrue(complete["valid"])
        self.assertAlmostEqual(complete["metrics"]["f0"], 1000.0)
        self.assertGreater(complete["metrics"]["q"], 5)

        clipped = q_from_sweep([(980.0, 0.8), (1000.0, 1.0), (1020.0, 0.9)])
        self.assertFalse(clipped["valid"])
        self.assertIn("3dB", clipped["warnings"][0])


class TdrAnalysisTest(unittest.TestCase):
    def test_tdr_detects_two_pulses_and_calculates_length(self):
        from experiments.analysis import analyze_tdr

        t = np.linspace(0, 100e-9, 2000)
        v = (
            np.exp(-((t - 10e-9) / 0.8e-9) ** 2)
            + 0.55 * np.exp(-((t - 50e-9) / 0.8e-9) ** 2)
        )
        result = analyze_tdr(t, v, velocity_factor=0.66)

        self.assertTrue(result["valid"])
        self.assertAlmostEqual(result["metrics"]["delta_t_s"], 40e-9, delta=0.4e-9)
        expected_length = 299_792_458.0 * 0.66 * 40e-9 / 2.0
        self.assertAlmostEqual(result["metrics"]["length_m"], expected_length, delta=0.05)

    def test_four_channel_propagation_fits_velocity_from_arrival_times(self):
        from experiments.analysis import analyze_propagation_4ch

        velocity = 2.0e8
        distances = [0, 10, 20, 30]
        t = np.linspace(-20e-9, 220e-9, 4000)
        channels = {}
        for idx, distance in enumerate(distances, start=1):
            arrival = 30e-9 + distance / velocity
            channels[idx] = 1 / (1 + np.exp(-(t - arrival) / 1.5e-9))

        result = analyze_propagation_4ch(t, channels, distances)

        self.assertTrue(result["valid"])
        self.assertAlmostEqual(result["metrics"]["velocity_m_s"], velocity, delta=velocity * 0.04)
        self.assertEqual(len(result["metrics"]["arrivals"]), 4)


class CouplingFitTest(unittest.TestCase):
    def test_coupling_fit_reports_inverse_cube_and_core_gain(self):
        from experiments.analysis import fit_coupling_points

        points = [
            {"distance_cm": 4, "angle_deg": 0, "core": False, "gain": 1 / 4**3},
            {"distance_cm": 6, "angle_deg": 0, "core": False, "gain": 1 / 6**3},
            {"distance_cm": 8, "angle_deg": 0, "core": False, "gain": 1 / 8**3},
            {"distance_cm": 4, "angle_deg": 0, "core": True, "gain": 3 / 4**3},
        ]
        result = fit_coupling_points(points)

        self.assertTrue(result["valid"])
        self.assertAlmostEqual(result["fit"]["distance_exponent"], -3.0, delta=0.05)
        self.assertAlmostEqual(result["fit"]["core_gain"], 3.0, delta=0.05)


class ImpedanceAnalysisTest(unittest.TestCase):
    def test_capacitor_impedance_estimates_capacitance_and_phase(self):
        from experiments.analysis import analyze_impedance_point

        f = 1000.0
        c = 100e-9
        rs = 1000.0
        t = np.linspace(0, 0.02, 4000, endpoint=False)
        current = 0.001 * np.cos(2 * np.pi * f * t)
        v_dut = (1 / (2 * np.pi * f * c)) * 0.001 * np.cos(2 * np.pi * f * t - np.pi / 2)
        v_ref = v_dut + current * rs

        out = analyze_impedance_point(t, v_ref, v_dut, rsense_ohm=rs, frequency_hz=f, component_hint="capacitor")

        self.assertTrue(out["valid"])
        self.assertAlmostEqual(out["metrics"]["capacitance_f"], c, delta=c * 0.08)
        self.assertLess(out["metrics"]["phase_deg"], -60)

    def test_inductor_impedance_estimates_inductance_and_phase(self):
        from experiments.analysis import analyze_impedance_point

        f = 5000.0
        l = 10e-3
        rs = 100.0
        t = np.linspace(0, 0.01, 4000, endpoint=False)
        current = 0.005 * np.cos(2 * np.pi * f * t)
        v_dut = (2 * np.pi * f * l) * 0.005 * np.cos(2 * np.pi * f * t + np.pi / 2)
        v_ref = v_dut + current * rs

        out = analyze_impedance_point(t, v_ref, v_dut, rsense_ohm=rs, frequency_hz=f, component_hint="inductor")

        self.assertTrue(out["valid"])
        self.assertAlmostEqual(out["metrics"]["inductance_h"], l, delta=l * 0.08)
        self.assertGreater(out["metrics"]["phase_deg"], 60)


class ApiContractTest(unittest.TestCase):
    def _handler(self):
        from experiments.web_server import ExperimentAPI

        return object.__new__(ExperimentAPI)

    def test_status_does_not_query_awg_idn(self):
        from experiments import web_server

        handler = self._handler()

        with mock.patch.object(web_server.bk, "idn", side_effect=lambda key: f"{key}-id") as idn:
            captured = handler._handle_get_api("/api/status", {})

        idn.assert_called_once_with("scope")
        self.assertEqual(captured["awg"]["id"], "write-only")
        self.assertEqual(captured["scope"]["id"], "scope-id")

    def test_q_sweep_endpoint_uses_uniform_response_shape(self):
        from experiments import web_server
        from experiments.q_measure import QResult, SweepPoint

        handler = self._handler()
        fake = QResult(
            f0=1000,
            peak_vrms=1.0,
            f1=950,
            f2=1050,
            bandwidth=100,
            q=10,
            sweep=[
                SweepPoint(900, 0.2),
                SweepPoint(950, 0.75),
                SweepPoint(1000, 1.0),
                SweepPoint(1050, 0.75),
                SweepPoint(1100, 0.2),
            ],
        )
        with mock.patch.object(web_server, "measure_q", return_value=fake):
            out = handler._handle_post("/api/exp/q-sweep", {"f_start": 900, "f_stop": 1100})

        self.assertTrue(out["ok"])
        self.assertTrue(out["valid"])
        self.assertIn("metrics", out)
        self.assertIn("raw", out)

    def test_experiment_post_routes_use_dispatch_table(self):
        from experiments import web_server

        self.assertIn("/api/exp/q-sweep", web_server.EXPERIMENT_POST_ROUTES)
        self.assertIn("/api/exp/propagation-4ch", web_server.EXPERIMENT_POST_ROUTES)
        self.assertIs(web_server.EXPERIMENT_POST_ROUTES["/api/exp/tdr-capture"], web_server._capture_tdr)

    def test_scope_config_turns_channels_on_when_requested(self):
        from experiments import web_server

        handler = self._handler()
        with mock.patch.object(web_server.scope, "channel_on") as channel_on:
            out = handler._handle_post("/api/scope/config", {"channel": 2, "on": True})

        self.assertTrue(out["ok"])
        channel_on.assert_called_once_with(2, True)

    def test_scope_autoset_endpoint_calls_scope_autoset(self):
        from experiments import web_server

        handler = self._handler()
        with mock.patch.object(web_server.scope, "autoset") as autoset:
            out = handler._handle_post("/api/scope/autoset", {})

        self.assertTrue(out["ok"])
        autoset.assert_called_once_with()

    def test_awg_screenshot_endpoint_exists_without_idn_query(self):
        from experiments import web_server

        handler = self._handler()
        with mock.patch.object(web_server.awg, "screenshot", return_value=b"png") as shot:
            out = handler._handle_get_api("/api/awg/screenshot", {})

        shot.assert_called_once()
        self.assertEqual(out["content_type"], "image/png")
        self.assertEqual(out["bytes"], b"png")

    def test_impedance_endpoint_uses_waveforms_without_voltage_measure_queries(self):
        from experiments import web_server

        handler = self._handler()
        f = 1000.0
        rs = 1000.0
        t = np.linspace(0, 0.02, 2000, endpoint=False)
        current = 0.001 * np.cos(2 * np.pi * f * t)
        v_dut = 1591.55 * 0.001 * np.cos(2 * np.pi * f * t - np.pi / 2)
        v_ref = v_dut + current * rs

        with mock.patch.object(web_server.scope, "get_waveforms", return_value={1: (t, v_ref), 2: (t, v_dut)}) as wf:
            out = handler._handle_post("/api/exp/impedance-point", {
                "frequency_hz": f,
                "rsense_ohm": rs,
                "component_hint": "capacitor",
            })

        wf.assert_called_once_with([1, 2])
        self.assertTrue(out["ok"])
        self.assertTrue(out["valid"])
        self.assertIn("impedance_ohm", out["metrics"])

    def test_propagation_endpoint_reads_four_scope_channels(self):
        from experiments import web_server

        handler = self._handler()
        t = np.linspace(0, 200e-9, 2000)
        data = {ch: (t, np.heaviside(t - ch * 20e-9, 0.0)) for ch in [1, 2, 3, 4]}
        with mock.patch.object(web_server.scope, "get_waveforms", return_value=data) as wf:
            out = handler._handle_post("/api/exp/propagation-4ch", {"tap_distances_m": "0,10,20,30"})

        wf.assert_called_once_with([1, 2, 3, 4])
        self.assertTrue(out["ok"])
        self.assertIn("velocity_m_s", out["metrics"])

    def test_panel_status_reports_awg_last_command_and_scope_channels(self):
        from experiments import web_server

        handler = self._handler()
        web_server.AWG_PANEL_STATE[1].update({"wave": "sine", "freq": 1000.0, "output": True})

        fake_channels = [
            {"channel": 1, "on": True, "scale": 0.5, "offset": 0.0, "coupling": "DC", "probe": 1.0},
            {"channel": 2, "on": False, "scale": 1.0, "offset": 0.0, "coupling": "AC", "probe": 10.0},
            {"channel": 3, "on": False, "scale": 1.0, "offset": 0.0, "coupling": "DC", "probe": 1.0},
            {"channel": 4, "on": True, "scale": 0.2, "offset": 0.0, "coupling": "DC", "probe": 1.0},
        ]
        with mock.patch.object(web_server.bk, "idn", side_effect=lambda key: f"{key}-id") as idn, \
             mock.patch.object(web_server.scope, "panel_status", return_value={
                 "online": True,
                 "id": "scope-id",
                 "channels": fake_channels,
                 "acquire_mode": "YT",
                 "trigger_status": "Trig'd",
             }), \
             mock.patch.object(web_server.dmm, "status", return_value={
                 "online": False,
                 "configured": True,
                 "port": "/dev/tty.usbserial-test",
             }), \
             mock.patch.object(web_server.lcr, "status", return_value={
                 "online": True,
                 "configured": True,
                 "devices": [{"product": "CP2110 HID USB-to-UART Bridge"}],
             }):
            out = handler._handle_get_api("/api/panel/status", {})

        idn.assert_not_called()
        self.assertEqual(out["awg"]["state_source"], "last_command")
        self.assertTrue(out["awg"]["channels"][0]["output"])
        self.assertEqual(len(out["scope"]["channels"]), 4)
        self.assertEqual(out["scope"]["state_source"], "instrument_query")
        self.assertEqual(out["dmm"]["port"], "/dev/tty.usbserial-test")
        self.assertTrue(out["lcr"]["online"])

    def test_dmm_read_endpoint_uses_dmm_driver(self):
        from experiments import web_server
        from instruments.dmm import DMMReading

        handler = self._handler()
        fake = DMMReading(
            value=0.5432,
            units="V",
            display="0.5432 V",
            raw_digits="05432",
            mode="V",
            range_index=1,
            dc=True,
            ac=False,
            auto=True,
            hold=False,
            relative=False,
            low_battery=False,
            data_valid=True,
            overload=False,
            timestamp=1.0,
        )
        with mock.patch.object(web_server.dmm, "read_once", return_value=fake) as read_once:
            out = handler._handle_post("/api/dmm/read", {"port": "/dev/tty.usbserial-test", "timeout": 0.5})

        read_once.assert_called_once_with("/dev/tty.usbserial-test", 0.5)
        self.assertTrue(out["ok"])
        self.assertEqual(out["reading"]["display"], "0.5432 V")

    def test_lcr_read_endpoint_uses_lcr_driver(self):
        from experiments import web_server
        from instruments.lcr import DisplayValue, LCRReading

        handler = self._handler()
        fake = LCRReading(
            raw_hex="00 0d",
            frequency="1KHz",
            frequency_hz=1000.0,
            battery_level=3,
            flags={"auto": True, "lcr": True, "parallel": False},
            sorting_tolerance="",
            main_mode="Ls",
            secondary_mode="Q",
            primary=DisplayValue("numeric", 100, 1, "uH", "10.0 uH", 10.0, False),
            secondary=DisplayValue("numeric", 1, 3, "", "0.001", 0.001, True),
            timestamp=1.0,
        )
        with mock.patch.object(web_server.lcr, "read_once", return_value=fake) as read_once:
            out = handler._handle_post("/api/lcr/read", {"timeout": 0.5})

        read_once.assert_called_once_with(0.5)
        self.assertTrue(out["ok"])
        self.assertEqual(out["reading"]["display"], "Ls 1KHz 10.0 uH | Q 0.001 [Auto LCR]")

    def test_static_root_is_absolute_and_contains_panel(self):
        from experiments import web_server

        self.assertTrue(web_server.STATIC_ROOT.is_absolute())
        self.assertTrue((web_server.STATIC_ROOT / "panel.html").exists())


if __name__ == "__main__":
    unittest.main()
