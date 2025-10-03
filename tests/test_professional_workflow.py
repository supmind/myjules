import pytest
import json
from unittest.mock import MagicMock, call
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
from minijules import app, tools, indexing
from minijules.result import ToolExecutionResult

# --- 模拟 CoreAgent 的逐步决策 ---
MOCK_AGENT_RESPONSES = [
    # 1. 创建依赖文件
    MagicMock(summary=json.dumps({"tool_name": "create_file", "parameters": {"filename": "requirements.txt", "content": "requests"}})),
    # 2. 安装依赖
    MagicMock(summary=json.dumps({"tool_name": "run_in_bash", "parameters": {"command": "pip install -r requirements.txt"}})),
    # 3. 编写失败的测试
    MagicMock(summary=json.dumps({"tool_name": "create_file", "parameters": {"filename": "tests/test_web.py", "content": "from web import get_google\ndef test_get_google(): assert get_google() == 200"}})),
    # 4. 运行测试（预期失败）
    MagicMock(summary=json.dumps({"tool_name": "run_tests_and_parse_report", "parameters": {"language": "py"}})),
    # 5. 编写实现代码
    MagicMock(summary=json.dumps({"tool_name": "create_file", "parameters": {"filename": "web.py", "content": "import requests\ndef get_google(): return requests.get('https://google.com').status_code"}})),
    # 6. 再次运行测试（预期成功）
    MagicMock(summary=json.dumps({"tool_name": "run_tests_and_parse_report", "parameters": {"language": "py"}})),
    # 7. 完成任务
    MagicMock(summary=json.dumps({"tool_name": "task_complete", "parameters": {"summary": "TDD workflow complete."}}))
]

@pytest.fixture
def setup_test_workspace(monkeypatch):
    """为 TDD 测试创建一个干净、隔离的工作区，并包含 tests 子目录。"""
    workspace_path = Path('./professional_tdd_workspace').resolve()
    if workspace_path.exists():
        shutil.rmtree(workspace_path)
    (workspace_path / "tests").mkdir(parents=True)
    monkeypatch.setattr(tools, 'WORKSPACE_DIR', workspace_path)
    # 同样需要更新 indexing 模块中的路径
    monkeypatch.setattr(indexing, 'WORKSPACE_DIR', workspace_path)
    yield workspace_path
    shutil.rmtree(workspace_path)

def test_jules_app_professional_tdd_workflow(mocker, monkeypatch, setup_test_workspace):
    """
    端到端集成测试：验证新的 JulesApp 能否引导 CoreAgent 完成一个完整的专业 TDD 工作流。
    """
    # 1. 模拟所有外部依赖和配置
    mocker.patch('minijules.app.load_llm_config', return_value=[{'model': 'mock'}])
    mocker.patch('argparse.ArgumentParser.parse_args', return_value=MagicMock(task="create a function", auto=True, max_steps=10))
    mocker.patch('minijules.indexing.index_workspace')
    mocker.patch('minijules.indexing.retrieve_context', return_value=[])
    mocker.patch('minijules.indexing.retrieve_memory', return_value=[])

    # 2. 模拟 Agent 的响应序列
    mock_initiate_chat = mocker.patch('minijules.app.user_proxy.initiate_chat', side_effect=MOCK_AGENT_RESPONSES)

    # 3. 模拟工具的行为和返回值
    mocker.patch('minijules.tools.create_file', return_value=ToolExecutionResult(success=True, result="File created."))
    mocker.patch('minijules.tools.run_in_bash', return_value=ToolExecutionResult(success=True, result="Pip install successful."))
    mock_run_tests = mocker.patch('minijules.tools.run_tests_and_parse_report', side_effect=[
        ToolExecutionResult(success=False, result="Test failed: ImportError"),
        ToolExecutionResult(success=True, result="All tests passed.")
    ])

    # 4. 运行主应用
    app.main()

    # 5. 断言
    assert mock_initiate_chat.call_count == len(MOCK_AGENT_RESPONSES), "Agent 应按计划被调用相应次数"
    assert mock_run_tests.call_count == 2, "run_tests_and_parse_report 应该被调用两次"

    # 验证 Agent 在决定编写实现代码之前，是否收到了测试失败的报告
    prompt_for_impl_step = mock_initiate_chat.call_args_list[4].kwargs['message']
    assert "工作历史" in prompt_for_impl_step
    assert "结果: [失败] Test failed: ImportError" in prompt_for_impl_step