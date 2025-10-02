import os
import subprocess
from pathlib import Path
from tree_sitter_language_pack import get_language, get_parser
import git

# 导入新的标准化结果类
from minijules.result import ToolExecutionResult

# --- 多语言配置中心 ---
LANGUAGE_CONFIG = {
    ".py": {"language": "python", "function_node_type": "function_definition", "class_node_type": "class_definition"},
    ".js": {"language": "javascript", "function_node_types": ["function_declaration", "method_definition", "variable_declarator"], "class_node_type": "class_declaration"},
    ".go": {"language": "go", "function_node_type": "function_declaration", "class_node_type": "type_spec"},
    ".rs": {"language": "rust", "function_node_type": "function_item", "class_node_type": "struct_item"}
}

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
        # 命令的成功与否由调用者判断，工具本身执行成功
        return ToolExecutionResult(success=True, result=output)
    except Exception as e: return ToolExecutionResult(success=False, result=f"运行命令时发生意外错误: {e}")

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