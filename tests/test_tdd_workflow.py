import pytest
import json
from unittest.mock import MagicMock
import os
import shutil
from pathlib import Path

# --- 模拟重量级依赖 ---
import sys
sys.modules['chromadb'] = MagicMock()
sys.modules['sentence_transformers'] = MagicMock()
sys.modules['git'] = MagicMock()
sys.modules['autogen'] = MagicMock()

# --- 安全导入 ---
from minijules import app, tools
from minijules.result import ToolExecutionResult

# --- TDD 测试场景与模拟数据 ---

# 1. Planner 的第一次响应：编写一个失败的测试
TDD_PLAN_1_WRITE_FAILING_TEST = json.dumps([
    {
        "tool_name": "create_file",
        "parameters": {
            "filename": "test_subtract.py",
            "content": "from logic import subtract\n\ndef test_subtract():\n    assert subtract(5, 2) == 3"
        }
    },
    {
        "tool_name": "run_tests_and_parse_report",
        "parameters": {"language": "py"}
    }
])

# 2. Planner 的第二次响应（在收到测试失败报告后）：编写实现代码以通过测试
TDD_PLAN_2_WRITE_IMPLEMENTATION = json.dumps([
    {
        "tool_name": "create_file",
        "parameters": {
            "filename": "logic.py",
            "content": "def subtract(a, b):\n    return a - b"
        }
    },
    {
        "tool_name": "run_tests_and_parse_report",
        "parameters": {"language": "py"}
    }
])

@pytest.fixture
def setup_test_workspace(monkeypatch):
    """为 TDD 测试创建一个干净、隔离的工作区。"""
    workspace_path = Path('./tdd_test_workspace').resolve()
    if workspace_path.exists():
        shutil.rmtree(workspace_path)
    workspace_path.mkdir()
    monkeypatch.setattr(tools, 'WORKSPACE_DIR', workspace_path)
    yield workspace_path
    shutil.rmtree(workspace_path)

def test_tdd_workflow(mocker, monkeypatch, setup_test_workspace):
    """
    端到端集成测试：验证完整的 TDD 工作流：
    1. Planner 生成一个创建失败测试的计划。
    2. Orchestrator 执行该计划，测试因 `ImportError` 而失败。
    3. Orchestrator 捕获失败并触发重规划。
    4. Planner 根据失败报告生成一个创建实现代码的计划。
    5. Orchestrator 执行修复计划，测试最终通过。
    """
    # 1. 模拟依赖和用户输入
    mocker.patch('minijules.app.load_llm_config', return_value=[{'model': 'mock'}])
    monkeypatch.setattr('builtins.input', lambda _: "")
    mocker.patch('argparse.ArgumentParser.parse_args', return_value=MagicMock(task="create a subtract function using TDD"))
    mocker.patch('minijules.indexing.index_workspace')
    mocker.patch('minijules.indexing.retrieve_context', return_value=[])

    # 2. 模拟 Planner 的两次响应
    planner_responses = [
        MagicMock(summary=TDD_PLAN_1_WRITE_FAILING_TEST),
        MagicMock(summary=TDD_PLAN_2_WRITE_IMPLEMENTATION)
    ]
    mock_planner_chat = mocker.patch('minijules.app.user_proxy.initiate_chat', side_effect=planner_responses)

    # 3. 监视文件创建工具
    spy_create_file = mocker.spy(tools, 'create_file')

    # 4. 运行主应用逻辑
    # 这将实际调用 `run_tests_and_parse_report`，这是一个真实的集成
    app.main()

    # 5. 断言
    # a. Planner 是否被调用了两次
    assert mock_planner_chat.call_count == 2, "Planner 应该被调用两次（一次生成测试，一次生成实现）"

    # b. 第二次调用 Planner 的提示中是否包含了第一次测试失败的报告
    replan_prompt = mock_planner_chat.call_args_list[1].kwargs['message']
    assert "测试失败" in replan_prompt
    assert "ImportError" in replan_prompt  # 第一次失败应该是导入错误

    # c. create_file 是否被调用了两次，内容正确
    assert spy_create_file.call_count == 2
    # 第一次调用：创建测试文件
    spy_create_file.call_args_list[0].assert_called_with(
        filename="test_subtract.py",
        content="from logic import subtract\n\ndef test_subtract():\n    assert subtract(5, 2) == 3"
    )
    # 第二次调用：创建实现文件
    spy_create_file.call_args_list[1].assert_called_with(
        filename="logic.py",
        content="def subtract(a, b):\n    return a - b"
    )