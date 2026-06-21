from __future__ import annotations

import json
import unittest
from unittest import mock
from pathlib import Path

import numpy as np


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

        nested = next(p for p in EXPERIMENT_PROFILES if p["key"] == "nested_coils_core")
        self.assertIn("原副边教学线圈", nested["name"])
        self.assertNotIn("大小套合", nested["name"])
        self.assertIn("可插拔铁芯", nested["summary"])
        self.assertTrue(nested["safety"])

        diode = next(p for p in EXPERIMENT_PROFILES if p["key"] == "diode_detector_parts")
        self.assertIn("二极管", diode["name"])
        self.assertIn("检波", diode["summary"])
        self.assertTrue(diode["safety"])

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

    def test_tdr_defaults_to_awg_square_edge_source(self):
        from experiments.profiles import EXPERIMENT_STATIONS

        tdr = next(s for s in EXPERIMENT_STATIONS if s["key"] == "pulse_tdr_length")
        self.assertIn("awg_square_tdr_source", tdr["materials"])
        self.assertEqual(tdr["acquisition"]["awg"]["wave"], "square")
        self.assertIn("慢边沿", tdr["goal"])
        self.assertNotEqual(tdr["acquisition"]["awg"].get("source"), "avalanche_pulser")

    def test_static_workbench_keeps_materials_in_experiment_detail_not_sidebar(self):
        html = Path("experiments/static/index.html").read_text(encoding="utf-8")

        self.assertNotIn("本实验材料</h2>", html)
        self.assertIn('id="experimentMaterials"', html)
        self.assertIn('class="childNav"', html)

    def test_static_workbench_has_dedicated_instrument_setup_panel(self):
        html = Path("experiments/static/index.html").read_text(encoding="utf-8")

        self.assertIn('id="instrumentSetup"', html)
        self.assertIn('id="awgSetup"', html)
        self.assertIn('id="scopeSetup"', html)
        self.assertIn("applyInstrumentSetup", html)
        self.assertIn("captureInstrumentScreens", html)
        self.assertIn("/api/awg/screenshot", html)

    def test_static_workbench_separates_instrument_apply_from_screen_capture(self):
        html = Path("experiments/static/index.html").read_text(encoding="utf-8")

        self.assertIn("抓取仪器屏幕", html)
        apply_start = html.index("function applyInstrumentSetup")
        capture_start = html.index("function captureInstrumentScreens")
        apply_function = html[apply_start:capture_start]
        self.assertNotIn("captureInstrumentScreens()", apply_function)

    def test_static_workbench_normalizes_instrument_screen_sizes(self):
        html = Path("experiments/static/index.html").read_text(encoding="utf-8")

        self.assertIn('class="screen-frame hint" id="awgScreen"', html)
        self.assertIn('class="screen-frame hint" id="scopeScreen"', html)
        self.assertIn(".screen-frame{", html)
        self.assertIn("aspect-ratio:16/9", html)
        self.assertIn("object-fit:contain", html)

    def test_static_workbench_renders_diode_va_results(self):
        html = Path("experiments/static/index.html").read_text(encoding="utf-8")

        self.assertIn("diode_va", html)
        self.assertIn("/api/exp/diode-va", html)
        self.assertIn("drawDiodeVA", html)
        self.assertIn("二极管伏安", html)

    def test_static_workbench_moves_full_records_to_separate_page(self):
        html = Path("experiments/static/index.html").read_text(encoding="utf-8")
        records = Path("experiments/static/records.html")

        self.assertNotIn('<section class="band records">', html)
        self.assertIn("/records.html", html)
        self.assertTrue(records.exists())


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

    def test_scope_config_turns_channels_on_when_requested(self):
        from experiments import web_server

        handler = self._handler()
        with mock.patch.object(web_server.scope, "channel_on") as channel_on:
            out = handler._handle_post("/api/scope/config", {"channel": 2, "on": True})

        self.assertTrue(out["ok"])
        channel_on.assert_called_once_with(2, True)

    def test_awg_screenshot_endpoint_exists_without_idn_query(self):
        from experiments import web_server

        handler = self._handler()
        with mock.patch.object(web_server.awg, "screenshot", return_value=b"png") as shot:
            out = handler._handle_get_api("/api/awg/screenshot", {})

        shot.assert_called_once()
        self.assertEqual(out["content_type"], "image/png")
        self.assertEqual(out["bytes"], b"png")


if __name__ == "__main__":
    unittest.main()
