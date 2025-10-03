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
    # 1. 写失败的测试
    MagicMock(summary=json.dumps({
        "tool_name": "create_file",
        "parameters": {
            "filename": "test_logic.py",
            "content": "from logic import add\ndef test_add():\n    assert add(1, 2) == 3"
        }
    })),
    # 2. 运行测试（预期失败）
    MagicMock(summary=json.dumps({
        "tool_name": "run_tests_and_parse_report",
        "parameters": {"language": "py"}
    })),
    # 3. 根据失败报告，编写实现
    MagicMock(summary=json.dumps({
        "tool_name": "create_file",
        "parameters": {
            "filename": "logic.py",
            "content": "def add(a, b):\n    return a + b"
        }
    })),
    # 4. 再次运行测试（预期成功）
    MagicMock(summary=json.dumps({
        "tool_name": "run_tests_and_parse_report",
        "parameters": {"language": "py"}
    })),
    # 5. 任务完成
    MagicMock(summary=json.dumps({
        "tool_name": "task_complete",
        "parameters": {"summary": "TDD workflow for add function complete."}
    }))
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
    yield workspace_path
    shutil.rmtree(workspace_path)

def test_jules_app_tdd_workflow(mocker, monkeypatch, setup_test_workspace):
    """
    端到端集成测试：验证新的 JulesApp 能否引导 CoreAgent 完成一个基础的 TDD 流程。
    这个测试会进行真实的文件写入和测试运行。
    """
    # 1. 模拟配置和 Agent 响应
    mocker.patch('minijules.app.load_llm_config', return_value=[{'model': 'mock'}])
    mocker.patch('argparse.ArgumentParser.parse_args', return_value=MagicMock(task="create an add function", auto=True, max_steps=10))
    mocker.patch('minijules.indexing.index_workspace')
    mocker.patch('minijules.indexing.retrieve_context', return_value=[])
    mocker.patch('minijules.indexing.retrieve_memory', return_value=[])
    mock_initiate_chat = mocker.patch('minijules.app.user_proxy.initiate_chat', side_effect=MOCK_AGENT_RESPONSES)

    # 2. 运行主应用
    app.main()

    # 3. 断言
    assert mock_initiate_chat.call_count == len(MOCK_AGENT_RESPONSES)

    # 验证 Agent 在决定编写实现代码之前，确实收到了测试失败的报告
    prompt_for_impl_step = mock_initiate_chat.call_args_list[2].kwargs['message']
    assert "工作历史" in prompt_for_impl_step
    # 检查是否有测试失败的迹象
    assert "失败" in prompt_for_impl_step
    assert "ImportError" in prompt_for_impl_step or "ModuleNotFoundError" in prompt_for_impl_step

    # 验证最终测试是否通过
    final_test_prompt = mock_initiate_chat.call_args_list[4].kwargs['message']
    assert "所有" in final_test_prompt and "测试都已通过" in final_test_prompt