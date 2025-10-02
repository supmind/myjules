import sys
from unittest.mock import MagicMock
import pytest
from pathlib import Path
import shutil

# --- 模拟（Mock）重量级、非必需的依赖 ---
sys.modules['chromadb'] = MagicMock()
sys.modules['sentence_transformers'] = MagicMock()

# 现在可以安全地导入被测试的模块了
from minijules import indexing

# --- 测试设置 ---
TEST_WORKSPACE_NAME = "temp_indexing_test_workspace"
TEST_WORKSPACE_DIR = Path(__file__).parent.resolve() / TEST_WORKSPACE_NAME

@pytest.fixture(scope="function", autouse=True)
def setup_and_teardown_test_workspace(monkeypatch):
    if TEST_WORKSPACE_DIR.exists():
        shutil.rmtree(TEST_WORKSPACE_DIR)
    TEST_WORKSPACE_DIR.mkdir()
    monkeypatch.setattr(indexing, 'WORKSPACE_DIR', TEST_WORKSPACE_DIR)
    yield
    if TEST_WORKSPACE_DIR.exists():
        shutil.rmtree(TEST_WORKSPACE_DIR)

# --- 测试用例 ---
def test_comment_association_and_extraction():
    """
    测试 extract_chunks 函数是否能正确地从文件中提取代码块，
    并把紧邻其上方的注释与它们关联起来。
    """
    # 1. 准备测试文件
    # 在不相关的注释和函数之间插入一个空行，以测试“无注释”的情况
    test_file_content = """
# 这是一个计算器类，用于执行基本的数学运算。
class Calculator:
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

    chunks_by_name = {chunk['metadata']['name']: chunk for chunk in chunks}

    # 验证 Calculator 类
    calculator_chunk = chunks_by_name.get("Calculator")
    assert calculator_chunk is not None
    expected_calculator_comment = "# 这是一个计算器类，用于执行基本的数学运算。"
    assert calculator_chunk['metadata']['comment'] == expected_calculator_comment
    assert f"// DOCS: {expected_calculator_comment}" in calculator_chunk['document']

    # 验证 say_hello 函数
    hello_chunk = chunks_by_name.get("say_hello")
    assert hello_chunk is not None
    expected_hello_comment = "# 它可以向指定的人问好。"
    assert hello_chunk['metadata']['comment'] == expected_hello_comment
    assert f"// DOCS: {expected_hello_comment}" in hello_chunk['document']

    # 验证没有注释的函数
    no_comment_chunk = chunks_by_name.get("function_without_comment")
    assert no_comment_chunk is not None
    assert no_comment_chunk['metadata']['comment'] == "无文档。", "带有空行的函数应该没有关联的注释"
    assert "// DOCS: 无文档。" in no_comment_chunk['document']