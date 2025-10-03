import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch

# --- 模拟重量级依赖 ---
with patch('autogen_ext.memory.chromadb.ChromaDBVectorMemory'):
    from minijules.app import JulesApp
    from minijules import tools

@pytest.mark.asyncio
async def test_app_initializes_and_patches_correctly(mocker):
    """
    一个非常简单的“健全性”测试，验证 App 能否在所有 patch 都生效的情况下成功初始化。
    """
    mocker.patch('argparse.ArgumentParser.parse_args', return_value=MagicMock(task="test", max_steps=1))
    mocker.patch('minijules.agents.OpenAIChatCompletionClient')
    mocker.patch('minijules.indexing.index_workspace', new_callable=AsyncMock)
    mocker.patch('minijules.indexing.code_rag_memory', MagicMock())
    mocker.patch('minijules.indexing.task_history_memory', MagicMock())

    try:
        JulesApp(task_string="test", config_list=[{'model': 'mock'}], max_steps=1)
    except Exception as e:
        pytest.fail(f"JulesApp 初始化失败，即使所有依赖都被模拟了: {e}")

@pytest.mark.asyncio
async def test_request_code_review_tool(mocker):
    """
    测试 _request_code_review 工具是否能正确构建提示并调用LLM。
    """
    # 1. 准备模拟数据
    test_task = "这是一个测试任务"
    mock_diff = "--- a/file.py\n+++ b/file.py\n@@ -1 +1 @@\n-print('hello')\n+print('world')"
    mock_review_response = "#Correct#\n代码看起来不错！"

    # 2. 模拟外部依赖
    mocker.patch('minijules.tools.git_diff', return_value=mock_diff)

    mocker.patch('minijules.agents.OpenAIChatCompletionClient')

    mock_llm_response = MagicMock()
    mock_llm_response.content = mock_review_response
    mock_reviewer_client = MagicMock()
    mock_reviewer_client.create = AsyncMock(return_value=mock_llm_response)
    mocker.patch('minijules.app.OpenAIChatCompletionClient', return_value=mock_reviewer_client)

    # 3. 初始化App
    app = JulesApp(task_string=test_task, config_list=[{'model': 'mock', 'api_key': 'mock_key'}])

    # 4. 调用被测试的工具
    result = await app._request_code_review()

    # 5. 验证
    mock_reviewer_client.create.assert_called_once()

    # **关键修复**: 从 `call_args.kwargs` 中获取关键字参数，而不是从 `call_args.args` 获取位置参数。
    messages = mock_reviewer_client.create.call_args.kwargs['messages']
    system_message = messages[0].content
    user_prompt = messages[1].content

    assert "您是一位资深的软件架构师" in system_message
    assert test_task in user_prompt
    assert mock_diff in user_prompt

    assert mock_review_response in result
    assert "代码评审结果" in result