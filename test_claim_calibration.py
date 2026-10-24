"""Offline tests for the manual Auto Claim calibration UI and data helpers.

Run on a desktop Python environment with Tkinter/Pillow installed:
    python test_claim_calibration.py
The tests stub screen automation and window-system integrations because they are
not needed to validate calibration geometry or persistence.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import types
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

SCRIPT_PATH = Path(__file__).with_name("pengirim_pesan_otomatis.py")


def _install_test_stubs():
    pyautogui = types.ModuleType("pyautogui")
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.05
    pyautogui.FailSafeException = type("FailSafeException", (Exception,), {})
    for name in ("screenshot", "position", "click", "moveTo", "hotkey", "press", "write", "keyDown", "keyUp", "size"):
        setattr(pyautogui, name, lambda *args, **kwargs: None)

    pygetwindow = types.ModuleType("pygetwindow")
    pygetwindow.getWindowsWithTitle = lambda *args, **kwargs: []
    pystray = types.ModuleType("pystray")
    winreg = types.ModuleType("winreg")
    winsound = types.ModuleType("winsound")
    for name, module in (
        ("pyautogui", pyautogui),
        ("pygetwindow", pygetwindow),
        ("pystray", pystray),
        ("winreg", winreg),
        ("winsound", winsound),
    ):
        sys.modules[name] = module


def _load_application_module():
    _install_test_stubs()
    spec = importlib.util.spec_from_file_location("pengirim_pesan_otomatis_test", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Tidak bisa memuat script: {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


APP = _load_application_module()


def valid_calibration():
    """Schema V2: kotak OCR yang hanya berisi rarity/kode dan print number."""
    return {
        "version": 2,
        "coordinate_space": "window_normalized",
        "region_kind": "rarity_print",
        "window_size": [1280, 720],
        "slots": {
            "1": {"button": {"x": 0.20, "y": 0.82}, "ocr": {"x": 0.05, "y": 0.20, "w": 0.25, "h": 0.12}},
            "2": {"button": {"x": 0.50, "y": 0.82}, "ocr": {"x": 0.375, "y": 0.20, "w": 0.25, "h": 0.12}},
            "3": {"button": {"x": 0.80, "y": 0.82}, "ocr": {"x": 0.70, "y": 0.20, "w": 0.25, "h": 0.12}},
        },
    }


def legacy_calibration():
    """V1 calibration used full-card boxes and must be recalibrated before claiming."""
    return {
        "version": 1,
        "coordinate_space": "window_normalized",
        "window_size": [1280, 720],
        "slots": {
            "1": {"button": {"x": 0.20, "y": 0.82}, "card": {"x": 0.05, "y": 0.20, "w": 0.25, "h": 0.50}},
            "2": {"button": {"x": 0.50, "y": 0.82}, "card": {"x": 0.375, "y": 0.20, "w": 0.25, "h": 0.50}},
            "3": {"button": {"x": 0.80, "y": 0.82}, "card": {"x": 0.70, "y": 0.20, "w": 0.25, "h": 0.50}},
        },
    }


class CalibrationDataTests(unittest.TestCase):
    def test_valid_calibration_normalizes(self):
        result = APP._normalize_claim_calibration(valid_calibration())
        self.assertIsNotNone(result)
        self.assertEqual(list(result["slots"]), ["1", "2", "3"])
        self.assertEqual(result["window_size"], [1280, 720])

    def test_rejects_incomplete_calibration(self):
        bad = valid_calibration()
        del bad["slots"]["3"]
        self.assertIsNone(APP._normalize_claim_calibration(bad))

    def test_rejects_out_of_bounds_rectangle(self):
        bad = valid_calibration()
        bad["slots"]["2"]["ocr"]["w"] = 0.9
        self.assertIsNone(APP._normalize_claim_calibration(bad))

    def test_rejects_non_finite_coordinates(self):
        bad = valid_calibration()
        bad["slots"]["1"]["button"]["x"] = float("nan")
        self.assertIsNone(APP._normalize_claim_calibration(bad))

    def test_compact_rarity_print_text_is_enough_for_bot_profiles(self):
        cases = [
            ("karuta", "345 · 3", "karuta"),
            ("yume", "SS #42", "yume"),
            ("index", "45 V 3 SS", "index"),
            ("shisui", "123-45", "shisui"),
            ("mazoku", "SSR #42", "mazoku"),
        ]
        for requested, compact_text, expected_profile in cases:
            with self.subTest(bot=requested, text=compact_text):
                config = {
                    "claim_bot": requested,
                    "tier_ss": "SS", "tier_s": "S", "tier_a": "A",
                    "tier_b": "B", "tier_c": "C",
                }
                score, label, profile, confidence = APP._score_card_text(compact_text, config)
                self.assertGreaterEqual(score, 0.0, label)
                self.assertEqual(profile, expected_profile)
                self.assertGreater(confidence, 0.0)

    def test_text_only_ocr_crop_detects_change_from_blank_baseline(self):
        from PIL import ImageDraw
        baseline = Image.new("RGB", (240, 60), "white")
        current = baseline.copy()
        draw = ImageDraw.Draw(current)
        draw.rectangle((8, 12, 94, 45), fill="black")
        fresh, mean_diff, changed_fraction = APP.App._freshness(current, baseline)
        self.assertTrue(fresh)
        self.assertGreater(mean_diff, 5.5)
        self.assertGreater(changed_fraction, 0.035)

    def test_legacy_full_card_calibration_is_preserved_but_not_ready_for_new_ocr(self):
        old = APP._normalize_claim_calibration(legacy_calibration())
        self.assertIsNotNone(old)
        self.assertEqual(old["version"], 1)
        self.assertFalse(APP._claim_calibration_is_ready(old))
        self.assertIsNone(APP._claim_calibration_to_absolute(old, (-1600, 100, 1280, 720)))

    def test_new_calibration_maps_combined_ocr_regions(self):
        result = APP._claim_calibration_to_absolute(valid_calibration(), (-1600, 100, 1280, 720))
        self.assertIsNotNone(result)
        self.assertEqual(result["1"]["button"], (-1344, 690))
        self.assertEqual(result["1"]["ocr"], (-1536, 244, 320, 86))
        self.assertTrue(APP._claim_calibration_is_ready(valid_calibration()))

    def test_manual_auto_claim_uses_combined_ocr_regions_and_clicks_best_verified_button(self):
        from PIL import ImageDraw
        app = object.__new__(APP.App)
        win = SimpleNamespace(left=0, top=0, width=1280, height=720, isMinimized=False)
        region = (0, 0, 1280, 720)
        baseline_image = Image.new("RGB", (1280, 720), "white")
        current_image = baseline_image.copy()
        draw = ImageDraw.Draw(current_image)
        # Simulasi teks padat di tiap kotak OCR agar freshness melewati ambang.
        for key in ("1", "2", "3"):
            box = APP._claim_calibration_to_absolute(valid_calibration(), (0, 0, 1280, 720))[key]["ocr"]
            x, y, w, h = box
            draw.rectangle((x + 12, y + 12, x + 82, y + 55), fill="black")

        app.log = lambda _message: None
        app._claim_scan_region = lambda *args, **kwargs: region
        app._screenshot_region = lambda _region: current_image.copy()
        scores = {"1": 10.0, "2": 40.0, "3": 20.0}
        app._evaluate_card_image = lambda _image, _message, key: (
            scores[key], f"uji {key}", "mazoku", 0.90
        )
        message = {
            "smart_claim": True, "claim_timeout": 3, "claim_mode": "auto",
            "claim_bot": "mazoku", "claim_calibration": valid_calibration(),
        }
        baseline = {"region": region, "image": baseline_image, "captured_at": 0}
        clicks = []
        original_click = APP.pyautogui.click
        APP.pyautogui.click = lambda position: clicks.append(position)
        try:
            result = app.check_and_click_card(message, win=win, baseline=baseline, anchor=(640, 600))
        finally:
            APP.pyautogui.click = original_click
        self.assertTrue(result)
        self.assertEqual(clicks, [(640, 590)])

    def test_manual_geometry_rejects_resized_window(self):
        app = object.__new__(APP.App)
        win = SimpleNamespace(left=0, top=0, width=1600, height=900, isMinimized=False)
        with self.assertRaisesRegex(ValueError, "berubah lebih dari 8%"):
            app._manual_claim_geometry({"claim_calibration": valid_calibration()}, win, (0, 0, 1600, 900))

    def test_maps_relative_coordinates_to_negative_monitor(self):
        result = APP._claim_calibration_to_absolute(valid_calibration(), (-1600, 100, 1280, 720))
        self.assertIsNotNone(result)
        self.assertEqual(result["1"]["button"], (-1344, 690))
        self.assertEqual(result["1"]["ocr"], (-1536, 244, 320, 86))

    def test_message_data_roundtrip_and_legacy_compatibility(self):
        message = APP.new_message(
            text="test", when=datetime.now() + timedelta(minutes=2), repeat="once",
            count=1, interval=60, claim_calibration=valid_calibration(),
        )
        normalized = APP.normalize(message)
        self.assertIsNotNone(normalized)
        self.assertEqual(normalized["claim_calibration"]["slots"]["2"]["button"]["x"], 0.5)
        legacy = dict(message)
        legacy.pop("claim_calibration", None)
        normalized_legacy = APP.normalize(legacy)
        self.assertIsNotNone(normalized_legacy)
        self.assertIsNone(normalized_legacy["claim_calibration"])
        # Data remains JSON-serializable for data.json save/load.
        json.loads(json.dumps(normalized))

    def test_edit_existing_message_persists_calibration(self):
        """Regression: editing an existing message must not discard calibration."""
        calibration = valid_calibration()
        fake_result = {
            "text": "edited message",
            "when": datetime.now() + timedelta(minutes=2),
            "repeat": "once", "count": 1, "interval": 60, "every_sec": 600,
            "enters": 1, "enter_pause": 1.0, "target": "Discord", "rel_pos": None,
            "active_start": "", "active_end": "", "max_sends": 0, "days": [],
            "seq_delay": 5, "channel": "", "simulation": False,
            "smart_claim": True, "claim_timeout": 15, "claim_bot": "auto",
            "claim_mode": "auto", "claim_btn1": "", "claim_btn2": "", "claim_btn3": "",
            "claim_calibration": calibration,
            "tier_ss": "SS", "tier_s": "S", "tier_a": "A", "tier_b": "B", "tier_c": "C",
        }

        class FakeMessageDialog:
            def __init__(self, _parent, _message):
                self.result = fake_result

        message = {"id": "test1234", "status": "Dijadwalkan", "enabled": True,
                   "claim_calibration": None}
        app = object.__new__(APP.App)
        app.running = False
        app.root = SimpleNamespace(wait_window=lambda _dialog: None)
        app.selected = lambda: message
        app.refresh = lambda: None
        app.save = lambda: None
        original_dialog = APP.MessageDialog
        APP.MessageDialog = FakeMessageDialog
        try:
            APP.App.edit(app)
        finally:
            APP.MessageDialog = original_dialog

        self.assertEqual(message["claim_calibration"], APP._normalize_claim_calibration(calibration))


class CalibrationDialogTests(unittest.TestCase):
    def test_message_dialog_contains_calibration_tab_and_saves_data(self):
        import tkinter as tk
        from tkinter import ttk

        root = tk.Tk()
        root.geometry("500x300+0+0")
        root.update()
        dialog = None
        try:
            dialog = APP.MessageDialog(root)
            root.update()
            self.assertIsNone(dialog.claim_calibration)
            notebooks = []

            def walk(widget):
                for child in widget.winfo_children():
                    if isinstance(child, ttk.Notebook):
                        notebooks.append(child)
                    walk(child)

            walk(dialog)
            self.assertTrue(notebooks, "Notebook pengaturan pesan tidak dibuat")
            tab_names = [notebooks[0].tab(tab, "text") for tab in notebooks[0].tabs()]
            self.assertIn("Smart Auto-Claim", tab_names)
            controls = []

            def collect_controls(widget):
                for child in widget.winfo_children():
                    controls.append(child)
                    collect_controls(child)

            collect_controls(dialog)
            self.assertTrue(any(
                isinstance(widget, ttk.Button) and "Buka kalibrasi koordinat manual" in widget.cget("text")
                for widget in controls
            ), "Tombol pembuka kalibrasi manual tidak ditemukan")
            dialog.txt.delete("1.0", "end")
            dialog.txt.insert("1.0", "tes kalibrasi")
            dialog.claim_calibration = valid_calibration()
            dialog.save()
            self.assertIsNotNone(dialog.result)
            self.assertIsNotNone(APP._normalize_claim_calibration(dialog.result["claim_calibration"]))
        finally:
            try:
                if dialog is not None and dialog.winfo_exists():
                    dialog.destroy()
            except Exception:
                pass
            root.destroy()

    def test_canvas_point_and_rectangle_capture(self):
        import tkinter as tk

        root = tk.Tk()
        root.geometry("320x120+0+0")
        root.update()
        dialog = None
        try:
            image = Image.new("RGB", (800, 600), "white")
            dialog = APP.ClaimCalibrationDialog(root, image)
            root.update()

            actions = [
                ("Tombol 1 — klik titik", "button", "1", (100, 80), None),
                ("Area OCR rarity + print 1 — tarik kotak", "ocr", "1", (30, 40), (230, 120)),
                ("Tombol 2 — klik titik", "button", "2", (400, 80), None),
                ("Area OCR rarity + print 2 — tarik kotak", "ocr", "2", (270, 40), (470, 120)),
                ("Tombol 3 — klik titik", "button", "3", (700, 80), None),
                ("Area OCR rarity + print 3 — tarik kotak", "ocr", "3", (510, 40), (710, 120)),
            ]
            for label, kind, key, start, end in actions:
                dialog.mode_var.set(label)
                start_event = SimpleNamespace(x=start[0], y=start[1])
                dialog._on_press(start_event)
                if kind == "ocr":
                    dialog._on_release(SimpleNamespace(x=end[0], y=end[1]))

            dialog._save()
            self.assertIsNotNone(dialog.result)
            self.assertEqual(dialog.result["version"], 2)
            self.assertEqual(dialog.result["region_kind"], "rarity_print")
            self.assertEqual(set(dialog.result["slots"]), {"1", "2", "3"})
            for key in ("1", "2", "3"):
                self.assertIn("button", dialog.result["slots"][key])
                self.assertIn("ocr", dialog.result["slots"][key])
                self.assertLess(dialog.result["slots"][key]["ocr"]["h"], 0.20)
            self.assertIsNotNone(APP._normalize_claim_calibration(dialog.result))
        finally:
            try:
                if dialog is not None and dialog.winfo_exists():
                    dialog.destroy()
            except Exception:
                pass
            root.destroy()


if __name__ == "__main__":
    unittest.main(verbosity=2)
