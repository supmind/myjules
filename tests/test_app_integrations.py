import pytest
from unittest.mock import MagicMock, AsyncMock, patch

# 模拟ChromaDB以隔离测试
with patch('autogen_ext.memory.chromadb.ChromaDBVectorMemory', MagicMock()):
    import minijules.tools as tools
    from minijules.app import JulesApp

@pytest.mark.asyncio
async def test_run_tests_and_debug_app_tool(mocker):
    """
    测试 tools.run_tests_and_debug_app 工具是否能正确地:
    1. 调用语言检测。
    2. 使用映射查找正确的测试命令。
    3. 使用该命令调用核心的 `tools.run_tests_and_debug` 函数。
    """
    # 1. 准备
    mock_language = "python"
    mock_final_result = "All tests passed!"

    # 模拟 app 依赖
    mocker.patch('minijules.app.create_core_agent')
    # 现在模拟 tools 模块中的 OpenAIChatCompletionClient
    mocker.patch('minijules.tools.OpenAIChatCompletionClient')

    # 模拟工具的调用
    mock_detect_lang = mocker.patch('minijules.tools.detect_project_language', return_value=mock_language)
    mock_core_debugger = mocker.patch(
        'minijules.tools.run_tests_and_debug',
        new_callable=AsyncMock,
        return_value=mock_final_result
    )

    # 2. 执行
    # 创建 app 实例，因为被测工具需要它
    app = JulesApp(task_string="test", config_list=[{'model': 'mock', 'api_key': 'mock'}])
    # 直接调用重构后的工具函数
    result = await tools.run_tests_and_debug_app(app)

    # 3. 验证
    mock_detect_lang.assert_called_once()

    mock_core_debugger.assert_called_once()
    call_args, call_kwargs = mock_core_debugger.call_args
    # 验证测试命令是否从 TEST_COMMAND_MAP 正确获取
    assert call_kwargs['test_command'] == tools.TEST_COMMAND_MAP[mock_language]

    assert result == mock_final_result