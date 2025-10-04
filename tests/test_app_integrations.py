import pytest
from unittest.mock import MagicMock, AsyncMock, patch

with patch('autogen_ext.memory.chromadb.ChromaDBVectorMemory', MagicMock()):
    from minijules.app import JulesApp

@pytest.mark.asyncio
async def test_app_run_tests_and_debug_wrapper(mocker):
    """
    测试 JulesApp.run_tests_and_debug 包装器方法是否能正确地:
    1. 调用语言检测。
    2. 使用映射查找正确的测试命令。
    3. 使用该命令调用核心的 tools.run_tests_and_debug 函数。
    """
    # 1. 准备
    mock_language = "python"
    mock_test_command = "python3 -m pytest"
    mock_final_result = "All tests passed!"

    # 模拟 app 依赖
    mocker.patch('minijules.app.create_core_agent')
    mocker.patch('minijules.app.OpenAIChatCompletionClient') # 必须模拟，因为它在被测试的方法中被实例化

    # 模拟工具的调用
    mock_detect_lang = mocker.patch('minijules.tools.detect_project_language', return_value=mock_language)
    mock_core_debugger = mocker.patch(
        'minijules.tools.run_tests_and_debug',
        new_callable=AsyncMock,
        return_value=mock_final_result
    )

    # 2. 执行
    app = JulesApp(task_string="test", config_list=[{'model': 'mock', 'api_key': 'mock'}])
    result = await app.run_tests_and_debug()

    # 3. 验证
    mock_detect_lang.assert_called_once()

    mock_core_debugger.assert_called_once()
    call_args, call_kwargs = mock_core_debugger.call_args
    assert call_kwargs['test_command'] == mock_test_command

    assert result == mock_final_result