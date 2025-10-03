import pytest
from unittest.mock import MagicMock, call
import json

from minijules import app, tools
from minijules.result import ToolExecutionResult

def test_run_in_bash_safety_check(mocker):
    """
    测试 run_in_bash 工具的安全检查机制。
    """
    # 1. 模拟一个危险的命令
    dangerous_command = "rm -rf /"

    # 2. 第一次调用，不带 force=True
    result = tools.run_in_bash(command=dangerous_command)

    # 3. 断言
    assert not result.success
    assert "CONFIRMATION_REQUIRED" in result.result
    assert "检测到潜在的危险命令" in result.result

def test_app_handles_dangerous_command_confirmation(mocker, monkeypatch):
    """
    测试 JulesApp 主循环能否正确处理危险命令的确认流程。
    """
    # --- 模拟场景：Agent 尝试执行一个危险命令，用户授权 ---

    # 1. 模拟依赖
    mocker.patch('minijules.app.load_llm_config', return_value=[{'model': 'mock'}])
    mocker.patch('argparse.ArgumentParser.parse_args', return_value=MagicMock(task="delete everything", auto=False)) # 强制非自动模式
    mocker.patch('minijules.indexing.index_workspace')
    mocker.patch('minijules.indexing.retrieve_memory', return_value=[])

    # 2. 模拟 Agent 的响应
    agent_response = json.dumps({"tool_name": "run_in_bash", "parameters": {"command": "rm -rf /"}})
    mock_initiate_chat = mocker.patch('minijules.app.user_proxy.initiate_chat', return_value=MagicMock(summary=agent_response))

    # 3. 模拟用户的输入
    # 第一次是主循环的确认，第二次是危险命令的授权
    monkeypatch.setattr('builtins.input', MagicMock(side_effect=["", "yes"]))

    # 4. 监视 run_in_bash 的调用
    spy_run_in_bash = mocker.spy(tools, 'run_in_bash')

    # 5. 运行主应用
    test_app = app.JulesApp(task_string="delete everything", auto_mode=False)
    # 只运行一轮循环以进行测试
    test_app.max_steps = 1
    test_app.run()

    # 6. 断言
    # a. run_in_bash 被调用了两次
    assert spy_run_in_bash.call_count == 2

    # b. 第一次调用是普通调用
    first_call_args = spy_run_in_bash.call_args_list[0]
    assert first_call_args.kwargs['command'] == "rm -rf /"
    assert first_call_args.kwargs.get('force') is None or first_call_args.kwargs.get('force') is False

    # c. 第二次调用是强制执行
    second_call_args = spy_run_in_bash.call_args_list[1]
    assert second_call_args.kwargs['command'] == "rm -rf /"
    assert second_call_args.kwargs['force'] is True