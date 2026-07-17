import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class BossServiceScriptTests(unittest.TestCase):
    def test_start_script_launches_panel_and_opens_browser(self):
        script = ROOT / "BossServiceStart.bat"

        self.assertTrue(script.exists())
        text = script.read_text(encoding="utf-8")

        self.assertIn("boss_java_apply_panel.py", text)
        self.assertIn("--port", text)
        self.assertIn("8766", text)
        self.assertIn("http://127.0.0.1:8766", text)
        self.assertIn(".venv\\Scripts\\python.exe", text)
        self.assertIn("%SystemRoot%\\System32\\WindowsPowerShell\\v1.0\\powershell.exe", text)
        self.assertIn("Start-Process", text)
        self.assertIn("Name -match", text)
        self.assertIn("boss_java_apply_panel.out.log", text)
        self.assertIn("boss_java_apply_panel.err.log", text)

    def test_stop_script_stops_only_panel_processes(self):
        script = ROOT / "BossServiceStop.bat"

        self.assertTrue(script.exists())
        text = script.read_text(encoding="utf-8")

        self.assertIn("%SystemRoot%\\System32\\WindowsPowerShell\\v1.0\\powershell.exe", text)
        self.assertIn("Win32_Process", text)
        self.assertIn("Name -match", text)
        self.assertIn("CommandLine", text)
        self.assertIn("boss_java_apply_panel.py", text)
        self.assertIn("Stop-Process", text)
        self.assertNotIn("chrome", text.lower())
        self.assertNotIn("msedge", text.lower())


if __name__ == "__main__":
    unittest.main()
