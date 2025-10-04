import sys
from unittest.mock import MagicMock, patch
import pytest
from pathlib import Path
import shutil

# 使用 patch 来精确模拟重量级依赖，避免在导入时就实例化
# 这解决了 'chromadb is not a package' 的问题
with patch('autogen_ext.memory.chromadb.ChromaDBVectorMemory', MagicMock()):
    from minijules import indexing

# --- 测试设置 ---
TEST_WORKSPACE_NAME = "temp_indexing_test_workspace"
TEST_WORKSPACE_DIR = Path(__file__).parent.resolve() / TEST_WORKSPACE_NAME

@pytest.fixture(scope="function", autouse=True)
def setup_and_teardown_test_workspace(monkeypatch):
    """为每个测试创建一个干净、隔离的工作区。"""
    if TEST_WORKSPACE_DIR.exists():
        shutil.rmtree(TEST_WORKSPACE_DIR)
    TEST_WORKSPACE_DIR.mkdir()
    monkeypatch.setattr(indexing, 'WORKSPACE_DIR', TEST_WORKSPACE_DIR)
    yield
    if TEST_WORKSPACE_DIR.exists():
        shutil.rmtree(TEST_WORKSPACE_DIR)

# --- 测试用例 ---
def test_docstring_and_comment_extraction():
    """
    测试 extract_chunks 函数是否能:
    1. 优先提取 Python 的 docstring。
    2. 在没有 docstring 的情况下，回退到关联紧邻其上方的注释。
    3. 在没有注释或有空行的情况下，标记为“无文档”。
    """
    # 1. 准备测试文件
    test_file_content = """
# 这是一个计算器类，但下面的 docstring 应该优先。
class Calculator:
    '''这是一个优先的文档字符串。'''
    def add(self, a, b):
        return a + b

# 这是一个独立的函数，用于打印问候语。
# 它可以向指定的人问好。
def say_hello(name):
    print(f"Hello, {name}")

# 这行注释与下面的函数无关，因为中间有空行。

def function_without_comment():
    pass
"""
    test_file_path = TEST_WORKSPACE_DIR / "test_math.py"
    test_file_path.write_text(test_file_content, encoding="utf-8")

    # 2. 执行块提取
    chunks = indexing.extract_chunks(test_file_path, "python")

    # 3. 验证结果
    assert len(chunks) == 3, "应该提取出3个代码块（1个类，2个函数）"

    # 将结果转换为更易于断言的字典
    chunks_by_name = {chunk['metadata']['name']: chunk for chunk in chunks}

    # 验证 Calculator 类 - 应提取 docstring
    calculator_chunk = chunks_by_name.get("Calculator")
    assert calculator_chunk is not None
    expected_calculator_comment = "这是一个优先的文档字符串。"
    # 检查 metadata 和拼接后的文档内容
    assert calculator_chunk['metadata']['comment'] == expected_calculator_comment
    assert f"DOCS: {expected_calculator_comment}" in calculator_chunk['content']

    # 验证 say_hello 函数 - 应提取紧邻的注释
    hello_chunk = chunks_by_name.get("say_hello")
    assert hello_chunk is not None
    # 当前逻辑只捕获紧邻的前一行注释
    expected_hello_comment = "# 它可以向指定的人问好。"
    assert hello_chunk['metadata']['comment'] == expected_hello_comment
    assert f"DOCS: {expected_hello_comment}" in hello_chunk['content']

    # 验证没有注释的函数
    no_comment_chunk = chunks_by_name.get("function_without_comment")
    assert no_comment_chunk is not None
    assert no_comment_chunk['metadata']['comment'] == "无文档。"
    assert "DOCS: 无文档。" in no_comment_chunk['content']