import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


def load_module():
    module_path = Path("/Users/min/AI/monthly-activity-skill/scripts/generate_monthly_report.py")
    spec = importlib.util.spec_from_file_location("generate_monthly_report", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class MonthlyActivitySkillTests(unittest.TestCase):
    def test_goal_header_is_simplified(self):
        module = load_module()
        self.assertEqual(module.HEADERS[0], "目标")

    def test_detect_dayflow_environment_reports_missing_app(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            result = module.detect_dayflow_environment(
                app_path=base / "Dayflow.app",
                db_path=base / "chunks.sqlite",
                db_path_explicit=False,
            )
        self.assertFalse(result.available)
        self.assertEqual(result.reason, "dayflow_app_missing")

    def test_detect_dayflow_environment_allows_explicit_db_override(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            db_path = base / "chunks.sqlite"
            db_path.write_text("placeholder", encoding="utf-8")
            result = module.detect_dayflow_environment(
                app_path=base / "Dayflow.app",
                db_path=db_path,
                db_path_explicit=True,
            )
        self.assertTrue(result.available)
        self.assertEqual(result.reason, "explicit_db_path")

    def test_format_effort_value_marks_dayflow_unavailable(self):
        module = load_module()
        self.assertEqual(module.format_effort_value([], dayflow_available=False), "未检测到 Dayflow，暂无法折算工时 / D")


if __name__ == "__main__":
    unittest.main()
