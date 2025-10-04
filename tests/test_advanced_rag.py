import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

# 这段 with patch 是必需的，因为 app 模块在顶层导入了这些
with patch('autogen_ext.memory.chromadb.ChromaDBVectorMemory', MagicMock()):
    from minijules.app import JulesApp

@pytest.mark.asyncio
async def test_run_method_orchestrates_advanced_rag_flow(mocker):
    """
    集成测试: 验证 JulesApp.run 方法是否能正确地编排高级RAG流程。

    本测试采用“为可测试性而重构”后的最终策略，直接模拟 `_retrieve_enhanced_context`
    方法，以精确地验证 `run` 方法的编排逻辑。
    """
    # 1. --- 准备 ---
    # 模拟输入
    original_task = "这是一个测试任务"

    # 模拟 _retrieve_enhanced_context 方法的返回值
    mock_enhanced_context = "这是由 _retrieve_enhanced_context 生成的、包含所有上下文的增强版任务"
    mock_retriever = mocker.patch(
        'minijules.app.JulesApp._retrieve_enhanced_context',
        new_callable=AsyncMock,
        return_value=mock_enhanced_context
    )

    # 模拟其他在 run 方法中被调用的函数
    mocker.patch('minijules.app.indexing.index_workspace', new_callable=AsyncMock)
    mock_group_chat_run = mocker.patch('autogen_agentchat.teams.RoundRobinGroupChat.run', new_callable=AsyncMock)

    # 模拟 agent 的创建以避免初始化错误
    mocker.patch('minijules.app.create_core_agent', return_value=MagicMock())

    # 2. --- 执行 ---
    # 初始化并运行 App
    # 使用一个最小化的配置，因为所有依赖都已被模拟
    app = JulesApp(task_string=original_task, config_list=[{'model': 'mock', 'api_key': 'mock'}])
    await app.run()

    # 3. --- 验证 ---
    # 验证 _retrieve_enhanced_context 是否被正确调用
    mock_retriever.assert_called_once()

    # 验证最终传递给 group_chat.run 的任务字符串是否正是我们模拟的返回值
    mock_group_chat_run.assert_called_once()
    final_task_prompt = mock_group_chat_run.call_args.kwargs['task']
    assert final_task_prompt == mock_enhanced_context

    print("高级RAG流程编排测试成功: `run` 方法正确调用了 `_retrieve_enhanced_context` 并将其结果传递给了群聊。")