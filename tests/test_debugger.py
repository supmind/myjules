import pytest
from unittest.mock import MagicMock, AsyncMock, patch

# 这段 with patch 是必需的，因为 tools 模块在顶层导入了这些
with patch('autogen_ext.memory.chromadb.ChromaDBVectorMemory', MagicMock()):
    from minijules import tools

# --- 测试 _parse_pytest_output ---

MOCK_PYTEST_FAILURE_OUTPUT = """
============================= test session starts ==============================
...
____________________________ test_example_failure ____________________________

self = <tests.test_example.TestExample object at 0x7f...>

    def test_addition_fails(self):
>       assert 1 + 1 == 3
E       assert (2 == 3)

tests/test_example.py:10: AssertionError
_________________ ERROR at setup of test_another_error _________________

    def setup_method(self, method):
>       raise ValueError("Setup failed")
E       ValueError: Setup failed

tests/test_another.py:5: ValueError
=========================== short test summary info ============================
FAILED tests/test_example.py::TestExample::test_addition_fails - assert (2 == 3)
ERROR tests/test_another.py::test_another_error
========================= 1 failed, 1 error in 0.1s ==========================
"""

def test_parse_pytest_output_extracts_failures_and_errors():
    """
    验证 _parse_pytest_output 函数是否能正确地从 pytest 输出中提取失败和错误信息。
    """
    failures = tools._parse_pytest_output(MOCK_PYTEST_FAILURE_OUTPUT)

    assert len(failures) == 2, "应该解析出2个失败/错误"

    # 验证第一个失败 (AssertionError)
    failure_1 = failures[0]
    assert failure_1['test_name'] == "test_example_failure"
    assert failure_1['filepath'] == "tests/test_example.py"
    assert failure_1['line_number'] == 10
    assert failure_1['error_type'] == "AssertionError"
    assert failure_1['error_message'] == "assert (2 == 3)"
    assert "assert 1 + 1 == 3" in failure_1['full_traceback']

    # 验证第二个错误 (ValueError)
    failure_2 = failures[1]
    assert failure_2['test_name'] == "ERROR at setup of test_another_error"
    assert failure_2['filepath'] == "tests/test_another.py"
    assert failure_2['line_number'] == 5
    assert failure_2['error_type'] == "ValueError"
    assert failure_2['error_message'] == "Setup failed"
    assert "raise ValueError(\"Setup failed\")" in failure_2['full_traceback']

# --- 接下来将是 run_tests_and_debug 的测试用例 ---

@pytest.mark.asyncio
async def test_run_tests_and_debug_succeeds_on_first_try(mocker):
    """
    验证当测试首次运行时就通过时，工具是否能正确报告成功并返回结果。
    """
    # 1. 准备
    mock_test_command = "pytest"
    mock_success_output = "======================== 10 passed in 1.0s ========================"

    # 模拟 run_in_bash_session 的返回值
    mock_run_bash = mocker.patch('minijules.tools.run_in_bash_session', return_value=mock_success_output)

    # 2. 执行
    result = await tools.run_tests_and_debug(test_command=mock_test_command, client=MagicMock())

    # 3. 验证
    mock_run_bash.assert_called_once_with(mock_test_command)
    assert "所有测试在第 1 次尝试中成功通过" in result
    assert mock_success_output in result


@pytest.mark.asyncio
async def test_run_tests_and_debug_succeeds_after_one_fix(mocker):
    """
    验证工具在测试首次失败后，能否成功地完成一轮“解析-生成-应用-验证”循环。
    """
    # 1. 准备
    mock_test_command = "pytest"
    mock_failure_output = MOCK_PYTEST_FAILURE_OUTPUT
    mock_success_output = "======================== 10 passed in 1.0s ========================"
    mock_file_content = "def test_addition_fails(self):\n    assert 1 + 1 == 3"
    mock_patch_content = "--- a/tests/test_example.py\n+++ b/tests/test_example.py\n@@ -1,2 +1,2 @@\n-    assert 1 + 1 == 3\n+    assert 1 + 1 == 2"

    # 模拟 run_in_bash_session 在第一次调用时失败，第二次成功
    mock_run_bash = mocker.patch('minijules.tools.run_in_bash_session', side_effect=[
        mock_failure_output,
        mock_success_output
    ])

    # 模拟文件读取
    mocker.patch('minijules.tools._get_safe_path', return_value=MagicMock())
    mocker.patch('pathlib.Path.read_text', return_value=mock_file_content)

    # 模拟补丁生成和应用
    mock_generate_patch = mocker.patch('minijules.tools._generate_fix_patch', new_callable=AsyncMock, return_value=mock_patch_content)
    mock_apply_patch = mocker.patch('minijules.tools.apply_patch', return_value="补丁已成功应用")

    # 2. 执行
    result = await tools.run_tests_and_debug(test_command=mock_test_command, client=MagicMock())

    # 3. 验证
    assert mock_run_bash.call_count == 2
    mock_generate_patch.assert_called_once()
    mock_apply_patch.assert_called_once_with("tests/test_example.py", mock_patch_content)
    assert "所有测试在第 2 次尝试中成功通过" in result
    assert mock_success_output in result


@pytest.mark.asyncio
async def test_run_tests_and_debug_fails_after_max_retries(mocker):
    """
    验证当测试在所有重试后仍然失败时，工具是否会正确地放弃并报告最终的失败结果。
    """
    # 1. 准备
    max_retries = 2
    mock_test_command = "pytest"

    # 模拟 run_in_bash_session 总是返回失败
    mock_run_bash = mocker.patch('minijules.tools.run_in_bash_session', return_value=MOCK_PYTEST_FAILURE_OUTPUT)

    # 模拟文件读取和补丁生成/应用
    mocker.patch('minijules.tools._get_safe_path')
    mocker.patch('pathlib.Path.read_text', return_value="file content")
    mocker.patch('minijules.tools._generate_fix_patch', new_callable=AsyncMock, return_value="mock patch")
    mocker.patch('minijules.tools.apply_patch', return_value="补丁已成功应用")

    # 2. 执行
    result = await tools.run_tests_and_debug(
        test_command=mock_test_command,
        client=MagicMock(),
        max_retries=max_retries
    )

    # 3. 验证
    # 总共运行次数 = 初始尝试 + max_retries
    assert mock_run_bash.call_count == max_retries + 1
    assert f"在达到 {max_retries + 1} 次尝试后，测试仍然失败" in result
    assert MOCK_PYTEST_FAILURE_OUTPUT in result


@pytest.mark.asyncio
async def test_run_tests_and_debug_handles_patch_application_failure(mocker):
    """
    验证当生成的补丁无法被应用时，工具是否能正确报告失败并执行回滚。
    """
    # 1. 准备
    mock_test_command = "pytest"
    mock_failure_output = MOCK_PYTEST_FAILURE_OUTPUT
    mock_file_content = "original content"

    mocker.patch('minijules.tools.run_in_bash_session', return_value=mock_failure_output)
    # 正确地模拟文件读取
    mock_path = MagicMock()
    mock_path.read_text.return_value = mock_file_content
    mocker.patch('minijules.tools._get_safe_path', return_value=mock_path)

    mocker.patch('minijules.tools._generate_fix_patch', new_callable=AsyncMock, return_value="bad patch")

    # 模拟补丁应用失败
    mock_apply_patch_failure_msg = "应用补丁失败: Hunk #1 FAILED at 1."
    mock_apply_patch = mocker.patch('minijules.tools.apply_patch', return_value=mock_apply_patch_failure_msg)

    # 模拟文件恢复
    mock_overwrite_file = mocker.patch('minijules.tools.overwrite_file_with_block')

    # 2. 执行
    result = await tools.run_tests_and_debug(test_command=mock_test_command, client=MagicMock())

    # 3. 验证
    mock_apply_patch.assert_called_once()
    assert "生成的补丁无法被应用" in result
    assert mock_apply_patch_failure_msg in result

    # 验证文件恢复逻辑是否被调用
    mock_overwrite_file.assert_called_once_with("tests/test_example.py", mock_file_content)