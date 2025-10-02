import pytest
import json
from unittest.mock import MagicMock

# --- 模拟（Mock）重量级、非必需的依赖 ---
# 这必须在导入被测试的模块之前完成，以防止 ModuleNotFoundError。
import sys
sys.modules['chromadb'] = MagicMock()
sys.modules['sentence_transformers'] = MagicMock()
sys.modules['git'] = MagicMock()
sys.modules['autogen'] = MagicMock()

# 现在可以安全地在顶层导入了，因为 app.py 没有导入副作用
from minijules import app, tools

# 定义一个模拟的 JSON 计划
MOCK_JSON_PLAN = json.dumps([
    {"tool_name": "git_create_branch", "parameters": {"branch_name": "feature/new-login-flow"}},
    {"tool_name": "create_file", "parameters": {"filename": "login.py", "content": "print('user logged in')"}},
    {"tool_name": "run_in_bash", "parameters": {"command": "python3 login.py"}}
])

def test_dynamic_tool_execution_orchestration(mocker, monkeypatch):
    """
    集成测试：验证主编排逻辑能否正确解析 JSON 计划并动态调用相应的工具。
    """
    # 1. 模拟配置加载，现在它在 main() 内部被调用
    mocker.patch('minijules.app.load_llm_config', return_value=[{'model': 'mock'}])

    # 2. 模拟 Planner 的回复
    mocker.patch('minijules.app.user_proxy.initiate_chat', return_value=MagicMock(summary=MOCK_JSON_PLAN))

    # 3. 监视（Spy on）将要被调用的工具函数
    spy_create_branch = mocker.spy(tools, 'git_create_branch')
    spy_create_file = mocker.spy(tools, 'create_file')
    spy_run_in_bash = mocker.spy(tools, 'run_in_bash')

    # 4. 模拟用户输入，自动批准所有步骤
    monkeypatch.setattr('builtins.input', lambda _: "")

    # 5. 模拟 argparse
    mock_args = MagicMock()
    mock_args.task = "implement login flow"
    mocker.patch('argparse.ArgumentParser.parse_args', return_value=mock_args)

    # 6. 模拟 RAG，使其不执行
    mocker.patch('minijules.indexing.index_workspace')
    mocker.patch('minijules.indexing.retrieve_context', return_value=[])

    # 7. 运行主应用逻辑
    app.main()

    # 8. 断言工具是否被以正确的参数调用了
    spy_create_branch.assert_called_once_with(branch_name="feature/new-login-flow")
    spy_create_file.assert_called_once_with(filename="login.py", content="print('user logged in')")
    spy_run_in_bash.assert_called_once_with(command="python3 login.py")