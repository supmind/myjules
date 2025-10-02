import os
import subprocess
from pathlib import Path
from tree_sitter_language_pack import get_language, get_parser
import git
import xml.etree.ElementTree as ET

# 导入新的标准化结果类
from minijules.result import ToolExecutionResult
import minijules.indexing as indexing

import json

# --- 多语言配置中心 ---
def load_language_config():
    """从 JSON 文件加载多语言配置。"""
    try:
        config_path = Path(__file__).parent / "language_config.json"
        with config_path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"错误: 无法加载或解析 language_config.json: {e}")
        return {}

LANGUAGE_CONFIG = load_language_config()

WORKSPACE_DIR = Path(__file__).parent.resolve() / "workspace"
WORKSPACE_DIR.mkdir(exist_ok=True)

def _get_safe_path(filepath: str) -> Path:
    absolute_filepath = (WORKSPACE_DIR / filepath).resolve()
    if WORKSPACE_DIR not in absolute_filepath.parents and absolute_filepath != WORKSPACE_DIR:
        raise ValueError(f"错误：路径 '{filepath}' 试图逃离允许的工作区。")
    return absolute_filepath

def _get_ast(filepath: Path):
    file_extension = filepath.suffix
    if file_extension not in LANGUAGE_CONFIG:
        raise ValueError(f"不支持的文件类型: {file_extension}")
    lang_config = LANGUAGE_CONFIG[file_extension]
    lang_name = lang_config["language"]
    language = get_language(lang_name)
    parser = get_parser(lang_name)
    content_bytes = filepath.read_bytes()
    tree = parser.parse(content_bytes)
    return tree, content_bytes, lang_config

def _find_node_recursively(node, criteria):
    if criteria(node): return node
    for child in node.children:
        found = _find_node_recursively(child, criteria)
        if found: return found
    return None

def _get_node_name(node, node_type, lang_config):
    """A helper function to robustly get the name of a class or function node."""
    if lang_config["language"] == 'javascript' and node_type == 'variable_declarator':
        name_node = node.child_by_field_name('name')
        value_node = node.child_by_field_name('value')
        if name_node and value_node and value_node.type == 'arrow_function':
            return name_node.text.decode('utf8')
    elif node_type in lang_config.get("function_node_types", []) or node_type == lang_config.get("class_node_type"):
         name_node = node.child_by_field_name("name")
         if name_node:
             return name_node.text.decode('utf8')
    # Go/Rust special cases for structs/types
    elif node_type in ['type_spec', 'struct_item']:
        name_node = node.children[0] if node.children else None
        if name_node:
            return name_node.text.decode('utf8')
    return None

def _traverse_for_structure(node, lang_config, indent_level=1):
    """Helper to traverse AST and build a structure string."""
    structure_list = []
    indent = "  " * indent_level

    class_type = lang_config.get("class_node_type")
    func_types = lang_config.get("function_node_types", [])

    is_class = node.type == class_type
    is_func = node.type in func_types

    if is_class or is_func:
        name = _get_node_name(node, node.type, lang_config)
        if name:
            node_kind = "class" if is_class else "def"
            structure_list.append(f"{indent}{node_kind} {name}")

            if is_class:
                # Find body and recurse
                body_node = next((c for c in node.children if 'body' in c.type or 'block' in c.type or 'declaration_list' in c.type or 'field_declaration_list' in c.type), None)
                if not body_node and lang_config['language'] == 'go': # Go specific body search
                     struct_type_node = next((c for c in node.children if c.type == 'struct_type'), None)
                     if struct_type_node: body_node = next((c for c in struct_type_node.children if c.type == 'field_declaration_list'), None)

                if body_node:
                    for child in body_node.children:
                        structure_list.extend(_traverse_for_structure(child, lang_config, indent_level + 1))
        return structure_list

    # Default recursion for other nodes
    for child in node.children:
        structure_list.extend(_traverse_for_structure(child, lang_config, indent_level))

    return structure_list

def list_project_structure() -> ToolExecutionResult:
    """
    Recursively scans the workspace, parses all supported files, and returns a
    tree-like structure of all classes, functions, and methods.
    """
    try:
        output_lines = ["Project Structure:"]

        for file_path in sorted(WORKSPACE_DIR.rglob('*')):
            if not file_path.is_file() or file_path.suffix not in LANGUAGE_CONFIG:
                continue

            relative_path = file_path.relative_to(WORKSPACE_DIR)
            output_lines.append(f"📁 {relative_path}")

            try:
                tree, _, lang_config = _get_ast(file_path)
                # We only traverse from the root's direct children to get top-level definitions
                for node in tree.root_node.children:
                    symbols = _traverse_for_structure(node, lang_config)
                    output_lines.extend(symbols)
            except Exception as e:
                output_lines.append(f"  (Error parsing file: {e})")

        result = "\n".join(output_lines)
        if len(output_lines) == 1:
            result = "No supported files found in the workspace."

        return ToolExecutionResult(success=True, result=result)
    except Exception as e:
        return ToolExecutionResult(success=False, result=f"Failed to list project structure: {e}")

def replace_function_definition(filename: str, function_name: str, new_function_code: str) -> ToolExecutionResult:
    try:
        safe_path = _get_safe_path(filename)
        tree, original_bytes, lang_config = _get_ast(safe_path)
        def criteria(node):
            if lang_config["language"] == 'javascript' and node.type == 'variable_declarator':
                name_node = node.child_by_field_name('name')
                value_node = node.child_by_field_name('value')
                if name_node and value_node and value_node.type == 'arrow_function':
                    return name_node.text.decode('utf8') == function_name
                return False
            if node.type in lang_config.get("function_node_types", [lang_config.get("function_node_type")]):
                name_node = node.child_by_field_name("name")
                if name_node and name_node.text.decode('utf8') == function_name: return True
            return False
        node_to_replace = _find_node_recursively(tree.root_node, criteria)
        if not node_to_replace:
            return ToolExecutionResult(success=False, result=f"错误: 在 '{filename}' 中未找到名为 '{function_name}' 的函数。")
        start, end = node_to_replace.start_byte, node_to_replace.end_byte
        new_bytes = original_bytes[:start] + new_function_code.encode('utf8') + original_bytes[end:]
        safe_path.write_bytes(new_bytes)
        return ToolExecutionResult(success=True, result=f"函数 '{function_name}' 在 '{filename}' 中已成功替换。")
    except Exception as e:
        return ToolExecutionResult(success=False, result=f"使用 AST 替换函数时发生意外错误: {e}")

def insert_into_class_body(filename: str, class_name: str, code_to_insert: str) -> ToolExecutionResult:
    try:
        safe_path = _get_safe_path(filename)
        tree, original_bytes, lang_config = _get_ast(safe_path)
        def criteria(node):
            if node.type == lang_config["class_node_type"]:
                name_node = node.child_by_field_name("name") or (node.children[0] if node.type in ['type_spec', 'struct_item'] and node.children else None)
                if name_node and name_node.text.decode('utf8') == class_name: return True
            return False
        class_node = _find_node_recursively(tree.root_node, criteria)
        if not class_node:
            return ToolExecutionResult(success=False, result=f"错误: 在 '{filename}' 中未找到名为 '{class_name}' 的类/结构体。")
        body_node = next((c for c in class_node.children if 'body' in c.type or 'block' in c.type or 'declaration_list' in c.type or 'field_declaration_list' in c.type), None)
        if not body_node and lang_config['language'] == 'go':
             struct_type_node = next((c for c in class_node.children if c.type == 'struct_type'), None)
             if struct_type_node: body_node = next((c for c in struct_type_node.children if c.type == 'field_declaration_list'), None)
        if not body_node:
            return ToolExecutionResult(success=False, result=f"错误: 在 '{filename}' 中找到 '{class_name}'，但无法确定其主体。")
        if body_node.named_child_count > 0:
            last_child = body_node.named_children[-1]
            indentation = " " * last_child.start_point[1]
            insertion_point = last_child.end_byte
            code_to_insert = "\n" + code_to_insert
        else:
            indentation = " " * (class_node.start_point[1] + 4)
            insertion_point = body_node.start_byte + 1
            code_to_insert = "\n" + code_to_insert
        indented_code = "\n".join(indentation + line for line in code_to_insert.splitlines())
        new_bytes = original_bytes[:insertion_point] + indented_code.encode('utf8') + original_bytes[insertion_point:]
        safe_path.write_bytes(new_bytes)
        return ToolExecutionResult(success=True, result=f"代码已成功插入到 '{class_name}' 的主体中。")
    except Exception as e:
        return ToolExecutionResult(success=False, result=f"使用 AST 插入类/结构体主体时发生意外错误: {e}")

def list_files(path: str = ".") -> ToolExecutionResult:
    try:
        safe_path = _get_safe_path(path)
        if not safe_path.is_dir(): return ToolExecutionResult(success=False, result=f"错误：'{path}' 不是一个目录。")
        items = [f"{item.name}/" if item.is_dir() else item.name for item in sorted(list(safe_path.iterdir()))]
        return ToolExecutionResult(success=True, result="\n".join(items) if items else "目录为空。")
    except Exception as e: return ToolExecutionResult(success=False, result=f"列出文件时发生意外错误: {e}")

def read_file(filename: str) -> ToolExecutionResult:
    try:
        safe_path = _get_safe_path(filename)
        if not safe_path.is_file(): return ToolExecutionResult(success=False, result=f"错误：文件 '{filename}' 未找到。")
        return ToolExecutionResult(success=True, result=safe_path.read_text(encoding='utf-8'))
    except Exception as e: return ToolExecutionResult(success=False, result=f"读取文件时发生意外错误: {e}")

def create_file(filename: str, content: str) -> ToolExecutionResult:
    try:
        safe_path = _get_safe_path(filename)
        safe_path.parent.mkdir(parents=True, exist_ok=True)
        safe_path.write_text(content, encoding='utf-8')
        return ToolExecutionResult(success=True, result=f"文件 '{filename}' 已成功创建/更新。")
    except Exception as e: return ToolExecutionResult(success=False, result=f"创建文件时发生意外错误: {e}")

def delete_file(filename: str) -> ToolExecutionResult:
    try:
        safe_path = _get_safe_path(filename)
        if not safe_path.is_file(): return ToolExecutionResult(success=False, result=f"错误：文件 '{filename}' 未找到。")
        safe_path.unlink()
        return ToolExecutionResult(success=True, result=f"文件 '{filename}' 已成功删除。")
    except Exception as e: return ToolExecutionResult(success=False, result=f"删除文件时发生意外错误: {e}")

def write_to_scratchpad(content: str) -> ToolExecutionResult:
    try:
        (WORKSPACE_DIR / ".scratchpad.md").open("a", encoding="utf-8").write(content + "\n")
        return ToolExecutionResult(success=True, result="内容已成功写入便签。")
    except Exception as e: return ToolExecutionResult(success=False, result=f"写入便签时发生意外错误: {e}")

def read_scratchpad() -> ToolExecutionResult:
    try:
        path = WORKSPACE_DIR / ".scratchpad.md"
        content = path.read_text(encoding="utf-8") if path.is_file() else "便签为空或不存在。"
        return ToolExecutionResult(success=True, result=content)
    except Exception as e: return ToolExecutionResult(success=False, result=f"读取便签时发生意外错误: {e}")

def run_in_bash(command: str) -> ToolExecutionResult:
    try:
        result = subprocess.run(command, shell=True, cwd=WORKSPACE_DIR, capture_output=True, text=True, check=False)
        output = f"STDOUT:\n{result.stdout}\n" if result.stdout else ""
        output += f"STDERR:\n{result.stderr}\n" if result.stderr else ""
        output += f"返回码: {result.returncode}"
        return ToolExecutionResult(success=True, result=output)
    except Exception as e: return ToolExecutionResult(success=False, result=f"运行命令时发生意外错误: {e}")

def run_tests_and_parse_report(language: str) -> ToolExecutionResult:
    """根据指定的语言运行测试，生成 JUnit-XML 报告，然后解析它以提供结构化的测试结果。"""
    try:
        file_extension = f".{language}"
        if file_extension not in LANGUAGE_CONFIG:
            return ToolExecutionResult(success=False, result=f"错误：不支持的测试语言: {language}")

        test_command = LANGUAGE_CONFIG[file_extension]["test_command"]
        report_path = WORKSPACE_DIR / "test-report.xml"

        subprocess.run(
            test_command, shell=True, cwd=WORKSPACE_DIR, capture_output=True, text=True, check=False
        )

        if not report_path.exists():
            return ToolExecutionResult(success=False, result="错误：测试报告文件 'test-report.xml' 未生成。可能是测试运行器本身失败了。")

        tree = ET.parse(report_path)
        root = tree.getroot()
        testsuite = root.find('testsuite')

        failures = int(testsuite.get('failures', 0))
        errors = int(testsuite.get('errors', 0))
        total_tests = int(testsuite.get('tests', 0))

        if failures == 0 and errors == 0:
            return ToolExecutionResult(success=True, result=f"所有 {total_tests} 个测试都已通过。")

        failure_summary = [f"测试失败：{failures+errors}/{total_tests} 个测试用例未通过。"]
        for testcase in testsuite.findall('testcase'):
            failure = testcase.find('failure')
            if failure is not None:
                failure_details = (f"  - 测试用例: {testcase.get('classname')}.{testcase.get('name')}\n"
                                   f"    失败信息: {failure.get('message')}\n"
                                   f"    详细信息:\n{failure.text.strip()}")
                failure_summary.append(failure_details)

            error = testcase.find('error')
            if error is not None:
                error_details = (f"  - 测试用例: {testcase.get('classname')}.{testcase.get('name')}\n"
                                 f"    错误信息: {error.get('message')}\n"
                                 f"    详细信息:\n{error.text.strip()}")
                failure_summary.append(error_details)

        return ToolExecutionResult(success=False, result="\n".join(failure_summary))
    except Exception as e:
        return ToolExecutionResult(success=False, result=f"运行测试和解析报告时发生意外错误: {e}")

def git_status() -> ToolExecutionResult:
    try:
        repo = git.Repo(WORKSPACE_DIR)
        return ToolExecutionResult(success=True, result=f"Git Status:\n{repo.git.status()}")
    except Exception as e: return ToolExecutionResult(success=False, result=f"获取 Git 状态时发生意外错误: {e}")

def git_diff(filepath: str = None) -> ToolExecutionResult:
    try:
        repo = git.Repo(WORKSPACE_DIR)
        diff = repo.git.diff([filepath] if filepath else None, head=not filepath)
        return ToolExecutionResult(success=True, result=f"Git Diff:\n{diff}" if diff else "无变更。")
    except Exception as e: return ToolExecutionResult(success=False, result=f"获取 Git diff 时发生意外错误: {e}")

def git_add(filepath: str) -> ToolExecutionResult:
    try:
        repo = git.Repo(WORKSPACE_DIR)
        repo.git.add(str(_get_safe_path(filepath)))
        return ToolExecutionResult(success=True, result=f"文件 '{filepath}' 已成功添加到暂存区。")
    except Exception as e: return ToolExecutionResult(success=False, result=f"Git add 操作失败: {e}")

def git_commit(message: str) -> ToolExecutionResult:
    try:
        repo = git.Repo(WORKSPACE_DIR)
        repo.config_writer().set_value("user", "name", "MiniJules").release()
        repo.config_writer().set_value("user", "email", "minijules@agent.ai").release()
        return ToolExecutionResult(success=True, result=f"成功提交变更:\n{repo.git.commit(m=message)}")
    except Exception as e: return ToolExecutionResult(success=False, result=f"Git commit 操作失败: {e}")

def git_create_branch(branch_name: str) -> ToolExecutionResult:
    try:
        repo = git.Repo(WORKSPACE_DIR)
        new_branch = repo.create_head(branch_name)
        new_branch.checkout()
        return ToolExecutionResult(success=True, result=f"已成功创建并切换到新分支: '{branch_name}'。")
    except Exception as e:
        return ToolExecutionResult(success=False, result=f"创建 Git 分支时发生意外错误: {e}")

def manage_dependency(language: str, package_name: str, action: str = "add") -> ToolExecutionResult:
    """
    Manages project dependencies for a given language.
    For python, it modifies requirements.txt. For JS, it would modify package.json.
    This should be followed by a `run_in_bash` command to install the dependencies.

    :param language: The programming language ('py' or 'js').
    :param package_name: The name of the package to add or remove.
    :param action: The action to perform ('add' or 'remove'). Defaults to 'add'.
    """
    try:
        if action not in ["add", "remove"]:
            return ToolExecutionResult(success=False, result=f"错误: 无效的操作 '{action}'。只允许 'add' 或 'remove'。")

        if language == "py":
            req_file = _get_safe_path("requirements.txt")

            if action == "add":
                with req_file.open("a", encoding="utf-8") as f:
                    f.write(f"\n{package_name}")
                return ToolExecutionResult(success=True, result=f"已将 '{package_name}' 添加到 requirements.txt。请稍后运行 'pip install'。")

            # Action is 'remove'
            if not req_file.is_file():
                 return ToolExecutionResult(success=True, result="requirements.txt 不存在，无需移除。")

            lines = req_file.read_text(encoding="utf-8").splitlines()
            original_line_count = len(lines)
            lines = [line for line in lines if not line.strip().startswith(package_name)]

            if len(lines) == original_line_count:
                return ToolExecutionResult(success=False, result=f"在 requirements.txt 中未找到要移除的包 '{package_name}'。")

            req_file.write_text("\n".join(lines), encoding="utf-8")
            return ToolExecutionResult(success=True, result=f"已从 requirements.txt 中移除 '{package_name}'。")

        elif language == "js":
            # For JS, it's often better to just run the command directly.
            # This tool can be a placeholder or a validator.
            # For now, we'll just return a message suggesting the correct bash command.
            if action == "add":
                cmd = f"npm install {package_name}"
                return ToolExecutionResult(success=True, result=f"建议：为 JS 依赖运行 `run_in_bash`，命令为: '{cmd}'")
            else: # remove
                cmd = f"npm uninstall {package_name}"
                return ToolExecutionResult(success=True, result=f"建议：为 JS 依赖运行 `run_in_bash`，命令为: '{cmd}'")
        else:
            return ToolExecutionResult(success=False, result=f"错误: 不支持的语言 '{language}'。")

    except Exception as e:
        return ToolExecutionResult(success=False, result=f"管理依赖时发生意外错误: {e}")

def retrieve_code_context(query: str, n_results: int = 3) -> ToolExecutionResult:
    """
    Retrieves code snippets from the indexed workspace that are semantically
    related to the given query. Call this when you need to understand existing
    code before reading or modifying a file.

    :param query: The query string to search for.
    :param n_results: The maximum number of results to return.
    """
    try:
        context_list = indexing.retrieve_context(query, n_results=n_results)
        if not context_list:
            return ToolExecutionResult(success=True, result="No relevant code context found.")

        result_str = "\n\n---\n\n".join(context_list)
        return ToolExecutionResult(success=True, result=result_str)
    except Exception as e:
        return ToolExecutionResult(success=False, result=f"Failed to retrieve code context: {e}")

def execute_tdd_cycle(feature_description: str, test_file_path: str, impl_file_path: str, agents: dict, language: str) -> ToolExecutionResult:
    """
    Executes a full Test-Driven Development cycle for a given feature.
    This is a high-level composite tool that orchestrates a sub-task.

    The cycle consists of:
    1. Generating a failing test based on the feature description.
    2. Running tests to confirm the failure.
    3. Generating implementation code to make the test pass.
    4. Running tests again to confirm success.

    :param feature_description: A clear, natural language description of the feature to be implemented.
    :param test_file_path: The path to the test file to be created.
    :param impl_file_path: The path to the implementation file to be created/modified.
    :param agents: A dictionary containing 'core_agent' and 'user_proxy' instances for sub-dialogues.
    :param language: The programming language for the test run.
    :return: A ToolExecutionResult summarizing the outcome of the TDD cycle.
    """
    core_agent = agents["core_agent"]
    user_proxy = agents["user_proxy"]

    cycle_history = []
    summary = f"TDD Cycle for: '{feature_description}'\n\n"

    try:
        # --- Step 1: Generate Failing Test ---
        print("--- [TDD Cycle] Step 1: Generating failing test... ---")
        test_gen_prompt = f"Based on the feature description '{feature_description}', write the code for a failing test in the file '{test_file_path}'. The test should fail because the implementation in '{impl_file_path}' does not exist yet. Only output the code content, without any explanation or markdown."
        chat_result = user_proxy.initiate_chat(core_agent, message=test_gen_prompt, max_turns=1, silent=True)
        test_code = chat_result.summary.strip().replace("```python", "").replace("```", "").strip()

        create_file(filename=test_file_path, content=test_code)
        cycle_history.append(f"1. Wrote failing test to '{test_file_path}'.")

        # --- Step 2: Confirm Test Fails ---
        print("--- [TDD Cycle] Step 2: Confirming test failure... ---")
        test_result_fail = run_tests_and_parse_report(language=language)
        if test_result_fail.success:
            return ToolExecutionResult(success=False, result=summary + "TDD cycle failed: The initial test passed unexpectedly.")
        cycle_history.append(f"2. Confirmed test failure: {test_result_fail.result[:100]}...")

        # --- Step 3: Generate Implementation ---
        print("--- [TDD Cycle] Step 3: Generating implementation... ---")
        impl_gen_prompt = f"The test in '{test_file_path}' failed with the error: '{test_result_fail.result}'. Write the implementation code in '{impl_file_path}' to make the test pass. Only output the code content, without any explanation or markdown."
        chat_result = user_proxy.initiate_chat(core_agent, message=impl_gen_prompt, max_turns=1, silent=True)
        impl_code = chat_result.summary.strip().replace("```python", "").replace("```", "").strip()

        create_file(filename=impl_file_path, content=impl_code)
        cycle_history.append(f"3. Wrote implementation to '{impl_file_path}'.")

        # --- Step 4: Confirm Test Passes ---
        print("--- [TDD Cycle] Step 4: Confirming test success... ---")
        test_result_success = run_tests_and_parse_report(language=language)
        if not test_result_success.success:
            return ToolExecutionResult(success=False, result=summary + f"TDD cycle failed: Tests still fail after implementation.\nError: {test_result_success.result}")
        cycle_history.append("4. Confirmed all tests now pass.")

        summary += "\n".join(cycle_history)
        return ToolExecutionResult(success=True, result=summary)

    except Exception as e:
        return ToolExecutionResult(success=False, result=f"An unexpected error occurred during the TDD cycle: {e}")