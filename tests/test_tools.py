import pytest
from pathlib import Path
import shutil

# 导入被测试的模块
from minijules import tools

# --- 测试设置 ---
TEST_WORKSPACE_NAME = "temp_tools_test_workspace"
TEST_WORKSPACE_DIR = Path(__file__).parent.resolve() / TEST_WORKSPACE_NAME

@pytest.fixture(scope="function", autouse=True)
def setup_and_teardown_test_workspace(monkeypatch):
    """为每个测试创建一个干净、隔离的工作区。"""
    if TEST_WORKSPACE_DIR.exists():
        shutil.rmtree(TEST_WORKSPACE_DIR)
    TEST_WORKSPACE_DIR.mkdir()
    monkeypatch.setattr(tools, 'WORKSPACE_DIR', TEST_WORKSPACE_DIR)
    yield
    if TEST_WORKSPACE_DIR.exists():
        shutil.rmtree(TEST_WORKSPACE_DIR)

# --- 工具测试 ---

def test_apply_patch_success():
    """测试 apply_patch 是否能成功应用一个有效的补丁。"""
    filename = "patch_me.txt"
    original_content = "line 1\nline 2\nline 3\n"
    patch_content = (
        f"--- a/{filename}\n"
        f"+++ b/{filename}\n"
        "@@ -1,3 +1,3 @@\n"
        " line 1\n"
        "-line 2\n"
        "+line two\n"
        " line 3\n"
    )
    expected_content = "line 1\nline two\nline 3\n"

    # 1. 创建初始文件
    create_result = tools.overwrite_file_with_block(filename, original_content)
    assert "成功" in create_result, f"测试设置失败: 无法创建文件: {create_result}"

    # 2. 应用补丁
    patch_result = tools.apply_patch(filename, patch_content)
    assert "成功" in patch_result, f"应用补丁失败: {patch_result}"

    # 3. 验证内容
    read_result = tools.read_file(filename)
    assert read_result == expected_content

def test_apply_patch_failure():
    """测试 apply_patch 在补丁不匹配时是否会失败并返回错误信息。"""
    filename = "patch_me_fail.txt"
    original_content = "some completely different content\n"
    patch_content = (
        f"--- a/{filename}\n"
        f"+++ b/{filename}\n"
        "@@ -1,3 +1,3 @@\n"
        " line 1\n"
        "-line 2\n"
        "+line two\n"
        " line 3\n"
    )

    # 1. 创建初始文件
    tools.overwrite_file_with_block(filename, original_content)

    # 2. 应用一个不匹配的补丁
    patch_result = tools.apply_patch(filename, patch_content)

    # 3. 验证它是否失败并返回了 stderr
    assert "失败" in patch_result
    assert "STDERR" in patch_result or "hunk FAILED" in patch_result.lower()