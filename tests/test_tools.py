import pytest
from pathlib import Path
import os
import shutil

# 导入被测试的模块
from minijules import tools
from minijules.result import ToolExecutionResult

# --- 测试设置 ---
TEST_WORKSPACE_NAME = "temp_multilang_test_workspace"
TEST_WORKSPACE_DIR = Path(__file__).parent.resolve() / TEST_WORKSPACE_NAME

@pytest.fixture(scope="function", autouse=True)
def setup_and_teardown_test_workspace(monkeypatch):
    if TEST_WORKSPACE_DIR.exists():
        shutil.rmtree(TEST_WORKSPACE_DIR)
    TEST_WORKSPACE_DIR.mkdir()
    monkeypatch.setattr(tools, 'WORKSPACE_DIR', TEST_WORKSPACE_DIR)
    yield
    if TEST_WORKSPACE_DIR.exists():
        shutil.rmtree(TEST_WORKSPACE_DIR)

# --- 多语言测试数据 ---
TEST_CASES = {
    "python": {
        "filename": "main.py", "class_name": "Calculator", "func_name": "say_hello",
        "original_code": "class Calculator:\n    def add(self, a, b):\n        return a + b\n\ndef say_hello():\n    print(\"Hello, Python!\")",
        "new_func_code": "def say_hello():\n    print(\"Hello, World!\")",
        "code_to_insert": "def subtract(self, a, b):\n    return a - b"
    },
    "javascript": {
        "filename": "app.js", "class_name": "Greeter", "func_name": "sayGoodbye",
        "original_code": "class Greeter {\n    constructor(name) {\n        this.name = name;\n    }\n}\n\nconst sayGoodbye = () => {\n    console.log(\"Goodbye, JS!\");\n};",
        "new_func_code": "const sayGoodbye = () => {\n    console.log(\"Farewell, JS!\");\n};",
        "code_to_insert": "greet() {\n    console.log(`Hello, ${this.name}`);\n}"
    },
    "go": {
        "filename": "main.go", "class_name": "Circle", "func_name": "sayHello",
        "original_code": "package main\n\nimport \"fmt\"\n\ntype Circle struct {\n    radius float64\n}\n\nfunc sayHello() {\n    fmt.Println(\"Hello, Go!\")\n}",
        "new_func_code": "func sayHello() {\n    fmt.Println(\"Hello, World!\")\n}",
        "code_to_insert": "diameter float64"
    },
    "rust": {
        "filename": "main.rs", "class_name": "Rectangle", "func_name": "say_hello",
        "original_code": "struct Rectangle {\n    width: u32,\n    height: u32,\n}\n\nfn say_hello() {\n    println!(\"Hello, Rust!\");\n}",
        "new_func_code": "fn say_hello() {\n    println!(\"Hello, World!\");\n}",
        "code_to_insert": "depth: u32,"
    }
}

@pytest.mark.parametrize("lang, case", TEST_CASES.items())
def test_replace_function_definition_multilang(lang, case):
    """参数化测试：验证 replace_function_definition 在所有支持的语言上都能工作。"""
    tools.create_file(case["filename"], case["original_code"])

    result = tools.replace_function_definition(case["filename"], case["func_name"], case["new_func_code"])

    assert result.success, f"[{lang}] 替换函数失败: {result.result}"
    assert "成功替换" in result.result

    content_result = tools.read_file(case["filename"])
    assert "World!" in content_result.result or "Farewell" in content_result.result, f"[{lang}] 文件内容未按预期更新"

    tools.delete_file(case["filename"])


@pytest.mark.parametrize("lang, case", TEST_CASES.items())
def test_insert_into_class_body_multilang(lang, case):
    """参数化测试：验证 insert_into_class_body 在所有支持的语言上都能工作。"""
    tools.create_file(case["filename"], case["original_code"])

    result = tools.insert_into_class_body(case["filename"], case["class_name"], case["code_to_insert"])

    assert result.success, f"[{lang}] 插入类/结构体主体失败: {result.result}"
    assert "成功插入" in result.result

    content_result = tools.read_file(case["filename"])
    assert case["code_to_insert"].splitlines()[0] in content_result.result, f"[{lang}] 文件内容未按预期更新"

    tools.delete_file(case["filename"])

def test_apply_patch_success():
    """测试 apply_patch 是否能成功应用一个有效的补丁。"""
    filename = "patch_me.txt"
    original_content = "line 1\nline 2\nline 3\n"
    # The patch paths (a/ and b/) should be relative to the CWD of the patch command,
    # which is the workspace dir. So we just use the filename.
    patch_content = (
        f"--- {filename}\n"
        f"+++ {filename}\n"
        "@@ -1,3 +1,3 @@\n"
        " line 1\n"
        "-line 2\n"
        "+line two\n"
        " line 3\n"
    )
    expected_content = "line 1\nline two\nline 3\n"

    # 1. 创建初始文件
    create_result = tools.create_file(filename, original_content)
    assert create_result.success, f"测试设置失败: 无法创建文件: {create_result.result}"

    # 2. 应用补丁
    patch_result = tools.apply_patch(filename, patch_content)
    assert patch_result.success, f"应用补丁失败: {patch_result.result}"
    assert "成功应用" in patch_result.result

    # 3. 验证内容
    read_result = tools.read_file(filename)
    assert read_result.success
    assert read_result.result == expected_content

    # 4. 清理
    tools.delete_file(filename)