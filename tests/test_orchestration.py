import pytest
import json
from unittest.mock import MagicMock

# --- 模拟（Mock）重量级、非必需的依赖 ---
import sys
sys.modules['chromadb'] = MagicMock()
sys.modules['sentence_transformers'] = MagicMock()
sys.modules['git'] = MagicMock()
sys.modules['autogen'] = MagicMock()

# 现在可以安全地在顶层导入了
from minijules import app, tools
from minijules.result import ToolExecutionResult

# --- 用于动态执行测试的模拟计划 ---
MOCK_JSON_PLAN = json.dumps([
    {"tool_name": "git_create_branch", "parameters": {"branch_name": "feature/new-login-flow"}},
    {"tool_name": "create_file", "parameters": {"filename": "login.py", "content": "print('user logged in')"}},
])

def test_dynamic_tool_execution_orchestration(mocker, monkeypatch):
    """集成测试：验证主编排逻辑能否正确解析 JSON 计划并动态调用相应的工具。"""
    mocker.patch('minijules.app.load_llm_config', return_value=[{'model': 'mock'}])
    mocker.patch('minijules.app.user_proxy.initiate_chat', return_value=MagicMock(summary=MOCK_JSON_PLAN))

    spy_create_branch = mocker.spy(tools, 'git_create_branch')
    spy_create_file = mocker.spy(tools, 'create_file')

    monkeypatch.setattr('builtins.input', lambda _: "")
    mocker.patch('argparse.ArgumentParser.parse_args', return_value=MagicMock(task="implement login flow"))
    mocker.patch('minijules.indexing.index_workspace')
    mocker.patch('minijules.indexing.retrieve_context', return_value=[])

    app.main()

    spy_create_branch.assert_called_once_with(branch_name="feature/new-login-flow")
    spy_create_file.assert_called_once_with(filename="login.py", content="print('user logged in')")

# --- 用于重规划测试的模拟计划 ---
MOCK_FAILED_PLAN = json.dumps([
    {"tool_name": "read_file", "parameters": {"filename": "non_existent_file.txt"}}
])
MOCK_CORRECTED_PLAN = json.dumps([
    {"tool_name": "create_file", "parameters": {"filename": "non_existent_file.txt", "content": "I exist now!"}},
    {"tool_name": "read_file", "parameters": {"filename": "non_existent_file.txt"}}
])

def test_replanning_on_tool_failure(mocker, monkeypatch):
    """集成测试：验证当工具执行失败时，Orchestrator 是否能正确触发重规划。"""
    mocker.patch('minijules.app.load_llm_config', return_value=[{'model': 'mock'}])

    mock_planner_responses = [MagicMock(summary=MOCK_FAILED_PLAN), MagicMock(summary=MOCK_CORRECTED_PLAN)]
    mock_initiate_chat = mocker.patch('minijules.app.user_proxy.initiate_chat', side_effect=mock_planner_responses)

    # 模拟 read_file 的行为：第一次失败，第二次成功
    mock_read_file_responses = [
        ToolExecutionResult(success=False, result="错误：文件 'non_existent_file.txt' 未找到。"),
        ToolExecutionResult(success=True, result="I exist now!")
    ]
    mock_read_file = mocker.patch('minijules.tools.read_file', side_effect=mock_read_file_responses)

    # 模拟 create_file 的行为
    mock_create_file = mocker.patch('minijules.tools.create_file', return_value=ToolExecutionResult(success=True, result="文件已创建。"))

    monkeypatch.setattr('builtins.input', lambda _: "")
    mocker.patch('argparse.ArgumentParser.parse_args', return_value=MagicMock(task="read a file"))
    mocker.patch('minijules.indexing.index_workspace')
    mocker.patch('minijules.indexing.retrieve_context', return_value=[])

    app.main()

    assert mock_initiate_chat.call_count == 2, "Planner 应该被调用两次"

    second_call_args = mock_initiate_chat.call_args_list[1]
    prompt_for_replan = second_call_args.kwargs['message']
    assert "失败的计划" in prompt_for_replan
    assert "错误：文件 'non_existent_file.txt' 未找到。" in prompt_for_replan

    mock_create_file.assert_called_once_with(filename="non_existent_file.txt", content="I exist now!")
    assert mock_read_file.call_count == 2, "read_file 应该被调用两次（一次失败，一次成功）"