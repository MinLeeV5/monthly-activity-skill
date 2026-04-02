import importlib.util
import sys
import unittest
from pathlib import Path


def load_module():
    module_path = Path("/Users/min/AI/monthly-activity-skill/scripts/read_lark_project_context.py")
    spec = importlib.util.spec_from_file_location("read_lark_project_context", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class ReadLarkProjectContextTests(unittest.TestCase):
    def test_parse_lark_url_extracts_table_and_view(self):
        module = load_module()
        parsed = module.parse_lark_url(
            "https://cvte-seewo.feishu.cn/wiki/UUmAwhKfOi7bADkmMGhcVBUrnJe?table=tblPKf4yy4eqJGvN&view=vew38m5EoI"
        )
        self.assertEqual(parsed["entry_type"], "wiki")
        self.assertEqual(parsed["token"], "UUmAwhKfOi7bADkmMGhcVBUrnJe")
        self.assertEqual(parsed["table_id"], "tblPKf4yy4eqJGvN")
        self.assertEqual(parsed["view_id"], "vew38m5EoI")

    def test_extract_doc_goals_prefers_goal_section(self):
        module = load_module()
        markdown = """# 项目背景

说明

## 项目目标

- 完成个人端录屏能力切换
- 保证旧链路平滑迁移

## 实施计划

- 方案评审
"""
        goals = module.extract_doc_goals(
            title="录屏 SDK 升级",
            markdown=markdown,
            resource={"source_url": "https://example.com/docx/abc", "resource_type": "docx"},
        )
        self.assertEqual(goals[0]["title"], "项目目标")
        self.assertIn("完成个人端录屏能力切换", goals[0]["summary"])


if __name__ == "__main__":
    unittest.main()
