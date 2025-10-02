import pytest
from pathlib import Path
import os
import shutil

# --- 测试设置 ---

# 使用一个固定的临时测试工作区，确保测试的隔离性
TEST_WORKSPACE_NAME = "temp_multilang_test_workspace"
TEST_WORKSPACE_DIR = Path(__file__).parent.resolve() / TEST_WORKSPACE_NAME

# 导入被测试的模块
from minijules import tools

@pytest.fixture(scope="function", autouse=True)
def setup_and_teardown_test_workspace(monkeypatch):
    """
    一个函数作用域的 fixture，它为每个测试函数创建和清理一个隔离的测试工作区。
    它还会使用 monkeypatch 来确保 `tools` 模块在测试期间使用这个临时工作区。
    """
    # 清理上一次可能失败的运行留下的痕迹
    if TEST_WORKSPACE_DIR.exists():
        shutil.rmtree(TEST_WORKSPACE_DIR)

    # 设置：创建临时工作区
    TEST_WORKSPACE_DIR.mkdir()

    # `tools` 模块已在顶部导入，现在我们可以安全地 patch 它
    monkeypatch.setattr(tools, 'WORKSPACE_DIR', TEST_WORKSPACE_DIR)

    yield # 将控制权交给测试运行器

    # 清理：递归地删除临时工作区及其所有内容
    if TEST_WORKSPACE_DIR.exists():
        shutil.rmtree(TEST_WORKSPACE_DIR)

# --- 多语言测试数据 ---

TEST_CASES = {
    "python": {
        "filename": "main.py",
        "class_name": "Calculator",
        "func_name": "say_hello",
        "original_code": """
class Calculator:
    def add(self, a, b):
        return a + b

def say_hello():
    print("Hello, Python!")
""",
        "new_func_code": """
def say_hello():
    print("Hello, World!")
""",
        "code_to_insert": "def subtract(self, a, b):\n    return a - b"
    },
    "javascript": {
        "filename": "app.js",
        "class_name": "Greeter",
        "func_name": "sayGoodbye",
        "original_code": """
class Greeter {
    constructor(name) {
        this.name = name;
    }
}

const sayGoodbye = () => {
    console.log("Goodbye, JS!");
};
""",
        "new_func_code": """
const sayGoodbye = () => {
    console.log("Farewell, JS!");
};
""",
        "code_to_insert": "greet() {\n    console.log(`Hello, ${this.name}`);\n}"
    },
    "go": {
        "filename": "main.go",
        "class_name": "Circle",
        "func_name": "sayHello",
        "original_code": """
package main

import "fmt"

type Circle struct {
    radius float64
}

func sayHello() {
    fmt.Println("Hello, Go!")
}
""",
        "new_func_code": """
func sayHello() {
    fmt.Println("Hello, World!")
}
""",
        "code_to_insert": "diameter float64"
    },
    "rust": {
        "filename": "main.rs",
        "class_name": "Rectangle",
        "func_name": "say_hello",
        "original_code": """
struct Rectangle {
    width: u32,
    height: u32,
}

fn say_hello() {
    println!("Hello, Rust!");
}
""",
        "new_func_code": """
fn say_hello() {
    println!("Hello, World!");
}
""",
        "code_to_insert": "depth: u32,"
    }
}

@pytest.mark.parametrize("lang, case", TEST_CASES.items())
def test_replace_function_definition_multilang(lang, case):
    """参数化测试：验证 replace_function_definition 在所有支持的语言上都能工作。"""
    tools.create_file(case["filename"], case["original_code"])

    result = tools.replace_function_definition(case["filename"], case["func_name"], case["new_func_code"])

    assert "成功替换" in result, f"[{lang}] 替换函数失败: {result}"

    content = tools.read_file(case["filename"])
    assert "World!" in content or "Farewell" in content, f"[{lang}] 文件内容未按预期更新"

    tools.delete_file(case["filename"])


@pytest.mark.parametrize("lang, case", TEST_CASES.items())
def test_insert_into_class_body_multilang(lang, case):
    """参数化测试：验证 insert_into_class_body 在所有支持的语言上都能工作。"""
    tools.create_file(case["filename"], case["original_code"])

    result = tools.insert_into_class_body(case["filename"], case["class_name"], case["code_to_insert"])

    assert "成功插入" in result, f"[{lang}] 插入类/结构体主体失败: {result}"

    content = tools.read_file(case["filename"])
    assert case["code_to_insert"].splitlines()[0] in content, f"[{lang}] 文件内容未按预期更新"

    tools.delete_file(case["filename"])