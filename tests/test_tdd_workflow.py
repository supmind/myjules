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

# --- 用于新的 run_in_bash 工作流的模拟 Agent 响应 ---
# 1. 编写一个失败的测试
MOCK_WRITE_TEST = json.dumps({
    "tool_name": "create_file",
    "parameters": {
        "filename": "test_logic.py",
        "content": "from logic import add\ndef test_add():\n    assert add(1, 2) == 3"
    }
})
# 2. 运行测试并预期失败
MOCK_RUN_TESTS_FAIL = json.dumps({
    "tool_name": "run_in_bash",
    "parameters": {"command": "python3 -m pytest"}
})
# 3. 根据失败编写实现
MOCK_WRITE_IMPL = json.dumps({
    "tool_name": "create_file",
    "parameters": {
        "filename": "logic.py",
        "content": "def add(a, b):\n    return a + b"
    }
})
# 4. 运行测试并预期成功
MOCK_RUN_TESTS_SUCCESS = json.dumps({
    "tool_name": "run_in_bash",
    "parameters": {"command": "python3 -m pytest"}
})
# 5. 完成任务
MOCK_TASK_COMPLETE = json.dumps({
    "tool_name": "task_complete",
    "parameters": {"summary": "TDD workflow for add function complete."}
})

MOCK_AGENT_RESPONSES = [
    MagicMock(summary=MOCK_WRITE_TEST),
    MagicMock(summary=MOCK_RUN_TESTS_FAIL),
    MagicMock(summary=MOCK_WRITE_IMPL),
    MagicMock(summary=MOCK_RUN_TESTS_SUCCESS),
    MagicMock(summary=MOCK_TASK_COMPLETE)
]

@pytest.fixture
def setup_test_workspace(monkeypatch):
    """为 TDD 测试创建一个干净、隔离的工作区。"""
    workspace_path = Path('./tdd_test_workspace').resolve()
    if workspace_path.exists():
        shutil.rmtree(workspace_path)
    workspace_path.mkdir()
    monkeypatch.setattr(tools, 'WORKSPACE_DIR', workspace_path)
    monkeypatch.setattr(indexing, 'WORKSPACE_DIR', workspace_path)
    # 也创建一个虚拟的 pytest.ini 以避免警告
    (workspace_path / "pytest.ini").write_text("[pytest]\n")
    yield workspace_path
    shutil.rmtree(workspace_path)

def test_jules_app_tdd_workflow_with_bash(mocker, monkeypatch, setup_test_workspace):
    """
    端到端测试：验证 JulesApp 能否引导 CoreAgent 使用 run_in_bash 完成一个 TDD 流程。
    """
    # 1. 模拟配置和外部依赖
    mocker.patch('minijules.app.load_llm_config', return_value=[{'model': 'mock'}])
    mocker.patch('argparse.ArgumentParser.parse_args', return_value=MagicMock(task="create an add function", auto=True, max_steps=10))
    mocker.patch('minijules.indexing.index_workspace')
    mocker.patch('minijules.indexing.retrieve_context', return_value=[])
    mocker.patch('minijules.indexing.retrieve_memory', return_value=[])
    mock_initiate_chat = mocker.patch('minijules.app.user_proxy.initiate_chat', side_effect=MOCK_AGENT_RESPONSES)

    # 2. 模拟 run_in_bash 在测试失败和成功时的行为
    mock_run_bash = mocker.patch('minijules.tools.run_in_bash', side_effect=[
        # 第一次调用失败
        ToolExecutionResult(success=True, result="STDOUT: ...\nSTDERR: ImportError: cannot import name 'add' from 'logic'\n..."),
        # 第二次调用成功
        ToolExecutionResult(success=True, result="STDOUT: ...\n1 passed in 0.01s\n...")
    ])

    # 3. 运行主应用
    app.main()

    # 4. 断言
    assert mock_initiate_chat.call_count == len(MOCK_AGENT_RESPONSES)

    # 验证 Agent 在收到测试失败报告后，被提示编写实现代码
    prompt_for_impl_step = mock_initiate_chat.call_args_list[2].kwargs['message']
    assert "工作历史" in prompt_for_impl_step
    assert "ImportError" in prompt_for_impl_step
    assert "cannot import name 'add'" in prompt_for_impl_step

    # 验证 Agent 在收到测试成功报告后，被提示进入最后一步
    prompt_for_final_step = mock_initiate_chat.call_args_list[4].kwargs['message']
    assert "1 passed" in prompt_for_final_step

    # 验证 run_in_bash 被以正确的命令调用了两次
    assert mock_run_bash.call_count == 2
    mock_run_bash.assert_has_calls([
        call(command="python3 -m pytest"),
        call(command="python3 -m pytest")
    ])