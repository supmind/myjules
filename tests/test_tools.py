import pytest
from pathlib import Path
import os
from unittest.mock import patch

# 在导入被测试的模块之前，我们必须确保 WORKSPACE_DIR 指向一个临时目录。
# 这是因为 tools.py 在模块加载时就定义了 WORKSPACE_DIR。
# 我们使用一个固定的临时测试工作区。
TEST_WORKSPACE_NAME = "temp_test_workspace_for_jules"
TEST_WORKSPACE_DIR = Path(__file__).parent.resolve() / TEST_WORKSPACE_NAME

# --- Pytest Fixture: 自动创建和清理测试工作区 ---

@pytest.fixture(scope="module", autouse=True)
def setup_and_teardown_test_workspace():
    """
    一个在测试会话开始时运行一次的 fixture。
    它负责创建临时的测试工作区，并在所有测试结束后清理它。
    """
    # 设置：创建临时工作区
    TEST_WORKSPACE_DIR.mkdir(exist_ok=True)

    # 使用 patch 来在整个测试模块的生命周期内覆盖 tools.WORKSPACE_DIR
    with patch('minijules.tools.WORKSPACE_DIR', TEST_WORKSPACE_DIR):
        # `yield` 将控制权交给测试运行器
        yield

    # 清理：递归地删除临时工作区及其所有内容
    import shutil
    if TEST_WORKSPACE_DIR.exists():
        shutil.rmtree(TEST_WORKSPACE_DIR)

# --- 现在可以安全地导入被测试的模块 ---
import minijules.tools as tools

# --- 测试用例 ---

def test_get_safe_path_valid():
    """测试 _get_safe_path 是否能正确处理工作区内的有效路径。"""
    # Action
    safe_path = tools._get_safe_path("test_file.txt")
    # Assert
    assert safe_path == TEST_WORKSPACE_DIR / "test_file.txt"

def test_get_safe_path_directory_traversal():
    """测试 _get_safe_path 是否能成功阻止目录穿越攻击。"""
    # Action & Assert
    with pytest.raises(ValueError, match="试图逃离允许的工作区"):
        tools._get_safe_path("../../../etc/passwd")

def test_get_safe_path_absolute_path():
    """测试 _get_safe_path 是否会拒绝工作区外的绝对路径。"""
    # Action & Assert
    with pytest.raises(ValueError, match="试图逃离允许的工作区"):
        tools._get_safe_path("/some/other/path/file.txt")

def test_create_read_and_delete_file():
    """集成测试：测试文件的创建、读取和删除功能。"""
    filename = "test_lifecycle.txt"
    content = "你好，世界！\nHello, World!"

    # 1. 创建文件
    result_create = tools.create_file(filename, content)
    assert "成功创建/更新" in result_create

    # 验证文件物理上存在
    file_path = TEST_WORKSPACE_DIR / filename
    assert file_path.exists()

    # 2. 读取文件
    read_content = tools.read_file(filename)
    assert read_content == content

    # 3. 删除文件
    result_delete = tools.delete_file(filename)
    assert "成功删除" in result_delete

    # 验证文件物理上已被删除
    assert not file_path.exists()

def test_read_nonexistent_file():
    """测试读取一个不存在的文件时是否返回预期的错误信息。"""
    # Action
    result = tools.read_file("nonexistent_file.txt")
    # Assert
    assert "错误：文件 'nonexistent_file.txt' 未找到" in result

def test_list_files():
    """测试 list_files 函数能否正确列出文件和目录。"""
    # 在一个干净的状态下测试
    # 创建一个测试子目录和文件
    test_subdir = TEST_WORKSPACE_DIR / "subdir"
    test_subdir.mkdir()
    (TEST_WORKSPACE_DIR / "a_file.txt").touch()
    (TEST_WORKSPACE_DIR / "b_file.txt").touch()

    # Action
    result = tools.list_files(".")

    # Assert
    # 结果应该是排序的，目录应该有斜杠
    expected_items = ["a_file.txt", "b_file.txt", "subdir/"]
    assert result.split('\n') == expected_items

    # 清理创建的文件和目录
    (TEST_WORKSPACE_DIR / "a_file.txt").unlink()
    (TEST_WORKSPACE_DIR / "b_file.txt").unlink()
    test_subdir.rmdir()

def test_run_in_bash():
    """测试 run_in_bash 是否能在工作区内执行命令并捕获输出。"""
    # Action: 在 bash 中执行 'echo' 命令
    result = tools.run_in_bash("echo 'hello from bash'")

    # Assert
    assert "STDOUT:" in result
    assert "hello from bash" in result
    assert "返回码: 0" in result

# --- AST 工具的测试用例 ---

# 用于 AST 操作的示例 Python 文件内容
SAMPLE_AST_FILE_CONTENT = """
class MyCalculator:
    def add(self, a, b):
        \"\"\"这是一个原始的 add 函数。\"\"\"
        return a + b

def standalone_function():
    \"\"\"一个独立的函数。\"\"\"
    return "original"
"""

def test_replace_function_definition_successfully():
    """测试 replace_function_definition 是否能正确替换一个独立的函数。"""
    filename = "ast_test_file.py"
    tools.create_file(filename, SAMPLE_AST_FILE_CONTENT)

    new_function_code = """def standalone_function():
    \"\"\"这是一个新的函数。\"\"\"
    return "replaced"
"""
    # Action
    result = tools.replace_function_definition(filename, "standalone_function", new_function_code)

    # Assert
    assert "成功替换" in result

    # 验证文件内容
    content = tools.read_file(filename)
    assert "这是一个新的函数" in content
    assert "这是一个原始的 add 函数" in content  # 确保文件的其他部分未受影响

    # 清理
    tools.delete_file(filename)


def test_insert_into_class_body_successfully():
    """测试 insert_into_class_body 是否能正确地向类中插入一个新方法。"""
    filename = "ast_test_file.py"
    tools.create_file(filename, SAMPLE_AST_FILE_CONTENT)

    new_method_code = """def subtract(self, a, b):
    \"\"\"这是一个新的 subtract 函数。\"\"\"
    return a - b
"""
    # Action
    result = tools.insert_into_class_body(filename, "MyCalculator", new_method_code)

    # Assert
    assert "成功插入" in result

    # 验证文件内容
    content = tools.read_file(filename)
    assert "这是一个新的 subtract 函数" in content
    assert "这是一个原始的 add 函数" in content  # 确保文件的其他部分未受影响

    # 清理
    tools.delete_file(filename)