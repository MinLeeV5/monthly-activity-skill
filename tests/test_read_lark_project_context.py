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

    def test_extract_doc_tasks_parses_weekly_table_for_current_user(self):
        module = load_module()
        markdown = """# 26-03

<lark-table rows="2" cols="6" header-row="true" header-column="true">
  <lark-tr>
    <lark-td>产品类别</lark-td>
    <lark-td>团队目标</lark-td>
    <lark-td>负责人</lark-td>
    <lark-td>共担人</lark-td>
    <lark-td>目标分解&关键里程碑计划</lark-td>
    <lark-td>3.3 ～ 3.9</lark-td>
  </lark-tr>
  <lark-tr>
    <lark-td>会记</lark-td>
    <lark-td>
      **新版个人端 V1 —— 3.6**
      交付：
      - 完成录制链路切换
      - 完成查看页改版
    </lark-td>
    <lark-td><mention-user id="ou_current"/></lark-td>
    <lark-td></lark-td>
    <lark-td>
      - 完成接口联调 3.4
      - 完成 UI 评审 3.5
    </lark-td>
    <lark-td>
      - [x] 完成接口联调 3.4
      - [ ] 完成 UI 联调 3.6
      待办：补齐截图回归
      调整：查看页交互延后到 3.8
    </lark-td>
  </lark-tr>
</lark-table>
"""
        tasks = module.extract_doc_tasks(
            title="智能会议项目周会-归档",
            markdown=markdown,
            resource={"source_url": "https://example.com/wiki/weekly", "resource_type": "docx"},
            current_user={"name": "李伟民", "open_id": "ou_current"},
        )
        self.assertEqual(len(tasks), 1)
        task = tasks[0]
        self.assertEqual(task["title"], "新版个人端 V1 —— 3.6")
        self.assertEqual(task["start_date"], "2026-03-01")
        self.assertEqual(task["due_date"], "2026-03-31")
        self.assertIn("新版个人端 V1 —— 3.6", task["mapped_goal"])
        self.assertIn("完成录制链路切换", task["mapped_key_results"])
        self.assertIn("完成接口联调 3.4", task["mapped_actions"])
        self.assertIn("待办：补齐截图回归", task["mapped_progress"])


if __name__ == "__main__":
    unittest.main()
