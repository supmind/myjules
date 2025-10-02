import pytest
import json
from unittest.mock import MagicMock, ANY
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

# --- TDD 专业工作流测试场景 ---

# 1. Planner 的第一次响应：安装依赖并编写一个失败的测试
PLAN_1_SETUP_AND_FAILING_TEST = json.dumps([
    {
        "tool_name": "create_file",
        "parameters": {"filename": "requirements.txt", "content": "requests"}
    },
    {
        "tool_name": "run_in_bash",
        "parameters": {"command": "python3 -m pip install -r requirements.txt"}
    },
    {
        "tool_name": "create_file",
        "parameters": {
            "filename": "tests/test_web.py",
            "content": "import requests\nfrom web import check_google_status\n\ndef test_check_google_status():\n    assert check_google_status() == 200"
        }
    },
    {
        "tool_name": "run_tests_and_parse_report",
        "parameters": {"language": "py"}
    }
])

# 2. Planner 的第二次响应：编写实现代码以通过测试
PLAN_2_IMPLEMENT_AND_PASSING_TEST = json.dumps([
    {
        "tool_name": "create_file",
        "parameters": {
            "filename": "web.py",
            "content": "import requests\n\ndef check_google_status():\n    try:\n        r = requests.get('https://www.google.com', timeout=5)\n        return r.status_code\n    except requests.exceptions.RequestException:\n        return -1"
        }
    },
    {
        "tool_name": "run_tests_and_parse_report",
        "parameters": {"language": "py"}
    }
])

@pytest.fixture
def setup_test_workspace(monkeypatch):
    """为 TDD 测试创建一个干净、隔离的工作区，并包含 tests 子目录。"""
    workspace_path = Path('./professional_tdd_workspace').resolve()
    if workspace_path.exists():
        shutil.rmtree(workspace_path)
    # 创建工作区和 tests 子目录
    (workspace_path / "tests").mkdir(parents=True)

    monkeypatch.setattr(tools, 'WORKSPACE_DIR', workspace_path)

    yield workspace_path

    shutil.rmtree(workspace_path)

def test_professional_tdd_workflow(mocker, monkeypatch, setup_test_workspace):
    """
    端到端集成测试：验证完整的专业 TDD 工作流。
    """
    # 1. 模拟依赖和用户输入
    mocker.patch('minijules.app.load_llm_config', return_value=[{'model': 'mock'}])
    monkeypatch.setattr('builtins.input', lambda _: "")
    mocker.patch('argparse.ArgumentParser.parse_args', return_value=MagicMock(task="create a function to check google status"))
    mocker.patch('minijules.indexing.index_workspace')
    mocker.patch('minijules.indexing.retrieve_context', return_value=[])

    # 2. 模拟 Planner 的两次响应
    planner_responses = [
        MagicMock(summary=PLAN_1_SETUP_AND_FAILING_TEST),
        MagicMock(summary=PLAN_2_IMPLEMENT_AND_PASSING_TEST)
    ]
    mock_planner_chat = mocker.patch('minijules.app.user_proxy.initiate_chat', side_effect=planner_responses)

    # 3. 监视文件创建和 bash 命令工具
    spy_create_file = mocker.spy(tools, 'create_file')
    spy_run_in_bash = mocker.spy(tools, 'run_in_bash')

    # 4. 运行主应用逻辑
    app.main()

    # 5. 断言
    # a. Planner 是否被调用了两次
    assert mock_planner_chat.call_count == 2, "Planner 应该被调用两次（一次生成测试，一次生成实现）"

    # b. 第二次调用 Planner 的提示中是否包含了第一次测试失败的报告
    replan_prompt = mock_planner_chat.call_args_list[1].kwargs['message']
    assert "测试失败" in replan_prompt
    assert "ImportError" in replan_prompt or "ModuleNotFoundError" in replan_prompt

    # c. 验证 create_file 的调用
    assert spy_create_file.call_count == 3, "create_file 应该被调用三次（reqs, test, impl）"
    spy_create_file.assert_any_call(filename="requirements.txt", content="requests")
    spy_create_file.assert_any_call(filename="tests/test_web.py", content=mocker.ANY)
    spy_create_file.assert_any_call(filename="web.py", content=mocker.ANY)

    # d. 验证 run_in_bash 的调用
    spy_run_in_bash.assert_called_once_with(command="python3 -m pip install -r requirements.txt")