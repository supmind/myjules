import pytest
from unittest.mock import MagicMock, call
import json

# 模拟重量级依赖
import sys
sys.modules['autogen'] = MagicMock()

# --- 安全导入 ---
from minijules import tools
from minijules.result import ToolExecutionResult

def test_execute_tdd_cycle_success(mocker):
    """
    测试 execute_tdd_cycle 复合工具能否成功编排一个完整的 TDD 流程。
    """
    # 1. 准备模拟参数
    mock_core_agent = MagicMock()
    mock_user_proxy = MagicMock()

    mock_agents = {
        "core_agent": mock_core_agent,
        "user_proxy": mock_user_proxy
    }

    # 模拟 Agent 在子对话中的响应
    mock_user_proxy.initiate_chat.side_effect = [
        # 第一次调用（生成测试）
        MagicMock(summary="def test_feature(): assert feature() == True"),
        # 第二次调用（生成实现）
        MagicMock(summary="def feature(): return True")
    ]

    # 2. 模拟内嵌工具的返回值
    mocker.patch('minijules.tools.create_file', return_value=ToolExecutionResult(success=True, result="File created."))
    mocker.patch('minijules.tools.run_tests_and_parse_report', side_effect=[
        ToolExecutionResult(success=False, result="Test failed: Feature not implemented."),
        ToolExecutionResult(success=True, result="All tests passed.")
    ])

    # 3. 执行复合工具
    result = tools.execute_tdd_cycle(
        feature_description="a simple feature",
        test_file_path="tests/test_feature.py",
        impl_file_path="feature.py",
        agents=mock_agents,
        language="py"
    )

    # 4. 断言
    assert result.success is True
    assert "TDD Cycle for: 'a simple feature'" in result.result
    assert "Confirmed all tests now pass." in result.result

    # 验证 user_proxy.initiate_chat 被调用了两次
    assert mock_user_proxy.initiate_chat.call_count == 2

    # 验证第一次调用是要求生成测试
    first_prompt = mock_user_proxy.initiate_chat.call_args_list[0].kwargs['message']
    assert "write the code for a failing test" in first_prompt

    # 验证第二次调用是要求生成实现，并包含了第一次失败的错误信息
    second_prompt = mock_user_proxy.initiate_chat.call_args_list[1].kwargs['message']
    assert "failed with the error: 'Test failed: Feature not implemented.'" in second_prompt
    assert "Write the implementation code" in second_prompt