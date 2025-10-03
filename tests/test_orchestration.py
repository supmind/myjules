import pytest
from unittest.mock import MagicMock, call
import json
import sys

# 模拟重量级依赖以加快测试速度并隔离单元
sys.modules['autogen'] = MagicMock()
sys.modules['chromadb'] = MagicMock()
sys.modules['sentence_transformers'] = MagicMock()
sys.modules['git'] = MagicMock()


# 现在可以安全地在顶层导入了
from minijules import app, tools, indexing
from minijules.result import ToolExecutionResult

# --- 用于新架构的模拟 Agent 响应 ---

# 模拟一个两步成功的计划
MOCK_STEP_1_LIST_FILES = json.dumps({
    "tool_name": "list_files",
    "parameters": {"path": "."}
})
MOCK_STEP_2_CREATE_FILE = json.dumps({
    "tool_name": "create_file",
    "parameters": {"filename": "hello.txt", "content": "world"}
})
MOCK_STEP_3_TASK_COMPLETE = json.dumps({
    "tool_name": "task_complete",
    "parameters": {"summary": "File created successfully."}
})

def test_jules_app_successful_workflow(mocker, monkeypatch):
    """
    集成测试：验证新的 JulesApp 迭代式工作流。
    模拟 Agent 逐步做出决策，并验证 App 是否正确执行每一步。
    """
    # 1. 模拟所有外部依赖
    mocker.patch('minijules.app.load_llm_config', return_value=[{'model': 'mock'}])
    mocker.patch('argparse.ArgumentParser.parse_args', return_value=MagicMock(task="create a file", auto=True, max_steps=10))
    mocker.patch('minijules.indexing.index_workspace')
    mocker.patch('minijules.indexing.retrieve_context', return_value=[])
    mocker.patch('minijules.indexing.retrieve_memory', return_value=[])

    # 2. 模拟 CoreAgent 的逐步响应
    agent_responses = [
        MagicMock(summary=MOCK_STEP_1_LIST_FILES),
        MagicMock(summary=MOCK_STEP_2_CREATE_FILE),
        MagicMock(summary=MOCK_STEP_3_TASK_COMPLETE)
    ]
    mock_initiate_chat = mocker.patch('minijules.app.user_proxy.initiate_chat', side_effect=agent_responses)

    # 3. 监视工具的实际调用
    spy_list_files = mocker.spy(tools, 'list_files')
    spy_create_file = mocker.spy(tools, 'create_file')

    # 4. 运行主应用
    app.main()

    # 5. 验证
    assert mock_initiate_chat.call_count == 3, "Agent 应该被调用三次来完成三步任务"
    spy_list_files.assert_called_once_with(path=".")
    spy_create_file.assert_called_once_with(filename="hello.txt", content="world")


# --- 用于失败处理测试的模拟响应 ---
MOCK_FAILING_READ = json.dumps({
    "tool_name": "read_file",
    "parameters": {"filename": "non_existent.txt"}
})

def test_jules_app_handles_tool_failure(mocker, monkeypatch):
    """
    集成测试：验证当一个工具执行失败时，失败信息会被正确地记录到工作历史中，
    并被包含在下一次发送给 CoreAgent 的提示中。
    """
    # 1. 模拟依赖
    mocker.patch('minijules.app.load_llm_config', return_value=[{'model': 'mock'}])
    mocker.patch('argparse.ArgumentParser.parse_args', return_value=MagicMock(task="read a file", auto=True, max_steps=10))
    mocker.patch('minijules.indexing.index_workspace')
    mocker.patch('minijules.indexing.retrieve_context', return_value=[])
    mocker.patch('minijules.indexing.retrieve_memory', return_value=[])

    # 2. 模拟 Agent 只发出一个失败的指令，然后完成任务
    agent_responses = [
        MagicMock(summary=MOCK_FAILING_READ),
        MagicMock(summary=MOCK_STEP_3_TASK_COMPLETE)
    ]
    mock_initiate_chat = mocker.patch('minijules.app.user_proxy.initiate_chat', side_effect=agent_responses)

    # 3. 模拟工具的失败行为
    mocker.patch('minijules.tools.read_file', return_value=ToolExecutionResult(success=False, result="File not found error"))

    # 4. 运行主应用
    app.main()

    # 5. 验证
    assert mock_initiate_chat.call_count == 2, "Agent 应该被调用两次"

    # 检查第二次调用时的提示，它必须包含第一次失败的结果
    second_call_args = mock_initiate_chat.call_args_list[1]
    prompt_for_second_step = second_call_args.kwargs['message']

    assert "工作历史" in prompt_for_second_step
    assert "动作: read_file" in prompt_for_second_step
    assert "结果: [失败] File not found error" in prompt_for_second_step