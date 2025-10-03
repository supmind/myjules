import pytest
import asyncio
import shutil
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# 导入真正的AutoGen组件
from autogen_agentchat.agents import CodeExecutorAgent
from autogen_ext.code_executors.local import LocalCommandLineCodeExecutor
from autogen_agentchat.messages import TextMessage
from autogen_core import CancellationToken

# 导入我们需要模拟或配置的本地模块
from minijules import tools

# --- Pytest Fixtures ---
@pytest.fixture
def setup_test_workspace(monkeypatch):
    """为每个测试创建一个干净、隔离的工作区。"""
    workspace_path = Path('./final_test_workspace').resolve()
    if workspace_path.exists():
        shutil.rmtree(workspace_path)
    workspace_path.mkdir()

    # 将工具模块的工作目录指向我们的测试工作区
    monkeypatch.setattr(tools, 'WORKSPACE_DIR', workspace_path)

    # 创建一个空的pytest配置文件
    (workspace_path / "pytest.ini").write_text("[pytest]\n")

    yield workspace_path
    shutil.rmtree(workspace_path)

# --- 真正的集成测试 ---

@pytest.mark.asyncio
async def test_code_executor_agent_handles_tdd_flow(setup_test_workspace):
    """
    这个测试通过模拟一个 "代码生成Agent" 发送消息，来验证真实的 "CodeExecutorAgent"
    是否能正确地执行一个TDD流程。
    """
    # 1. 设置一个真实的 CodeExecutorAgent
    # 它将在我们隔离的工作区中执行代码
    code_executor = LocalCommandLineCodeExecutor(work_dir=setup_test_workspace)
    code_executor_agent = CodeExecutorAgent(
        name="TestCodeExecutor",
        code_executor=code_executor,
    )

    # 定义一个取消令牌，尽管在这个测试中我们不会使用它
    cancellation_token = CancellationToken()

    # --- 第1步: 模拟CoreAgent发送一个失败的测试代码 ---
    fail_test_content = """
from calculator import add

def test_add():
    assert add(2, 3) == 5
"""
    # 模拟CoreAgent生成的消息
    write_test_message = TextMessage(
        content=f"好的，这是第一步，我将编写一个失败的测试。\n```python\n{fail_test_content}\n```",
        source="MockCoreAgent"
    )
    # 将此消息写入一个文件中，以供CodeExecutorAgent执行
    (setup_test_workspace / "test_calculator.py").write_text(fail_test_content)

    # --- 第2步: 模拟CoreAgent请求运行测试 ---
    run_test_message = TextMessage(
        content="现在我将运行测试，预期它会因为缺少 `calculator` 模块而失败。\n```sh\npython3 -m pytest\n```",
        source="MockCoreAgent"
    )

    # **执行**: 将 "运行测试" 消息发送给 CodeExecutorAgent
    response = await code_executor_agent.on_messages([run_test_message], cancellation_token)

    # **验证**: 检查返回消息的内容
    response_text = response.chat_message.content
    assert "ModuleNotFoundError: No module named 'calculator'" in response_text

    # --- 第3步: 模拟CoreAgent发送实现代码 ---
    impl_code_content = """
def add(a, b):
    return a + b
"""
    # 模拟CoreAgent生成的消息
    write_impl_message = TextMessage(
        content=f"测试失败了，正如预期。现在我将编写实现代码。\n```python\n{impl_code_content}\n```",
        source="MockCoreAgent"
    )
    # 将实现代码写入文件
    (setup_test_workspace / "calculator.py").write_text(impl_code_content)

    # --- 第4步: 模拟CoreAgent再次请求运行测试 ---
    run_test_again_message = TextMessage(
        content="实现代码已编写完毕。现在我将再次运行测试，预期它会通过。\n```sh\npython3 -m pytest\n```",
        source="MockCoreAgent"
    )

    # **执行**: 将 "再次运行测试" 消息发送给 CodeExecutorAgent
    response = await code_executor_agent.on_messages([run_test_again_message], cancellation_token)

    # **验证**: 检查返回消息的内容
    response_text = response.chat_message.content
    assert "passed" in response_text.lower()
    assert "failures" not in response_text.lower()

    print("\n✅ 真实的CodeExecutorAgent已成功通过模拟的TDD流程！")