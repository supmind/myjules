import os
import subprocess
from pathlib import Path

# 定义工作区目录，确保所有文件操作都在此沙盒内进行。
# Path(__file__).parent.resolve() 指向 minijules/ 目录
WORKSPACE_DIR = Path(__file__).parent.resolve() / "workspace"

# 确保工作区目录存在
WORKSPACE_DIR.mkdir(exist_ok=True)

def _get_safe_path(filepath: str) -> Path:
    """
    将相对路径解析为工作区内的绝对路径。
    如果路径试图逃离工作区，则会引发 ValueError。
    """
    # 从提供的工作区路径和用户输入的文件路径创建绝对路径。
    # resolve() 方法会处理 '..' 等路径遍历序列。
    absolute_filepath = (WORKSPACE_DIR / filepath).resolve()

    # 检查解析后的路径是否仍在 WORKSPACE_DIR 的控制范围内。
    # 这是关键的安全边界。
    if WORKSPACE_DIR not in absolute_filepath.parents and absolute_filepath != WORKSPACE_DIR:
        raise ValueError(f"错误：路径 '{filepath}' 试图逃离允许的工作区。")

    return absolute_filepath


def list_files(path: str = ".") -> str:
    """
    列出工作区内给定路径下的文件和目录。
    以字符串形式返回文件和目录列表。
    """
    try:
        safe_path = _get_safe_path(path)
        if not safe_path.is_dir():
            return f"错误：'{path}' 不是一个目录。"

        items = []
        for item in sorted(list(safe_path.iterdir())):
            if item.is_dir():
                items.append(f"{item.name}/")
            else:
                items.append(item.name)

        return "\n".join(items) if items else "目录为空。"
    except ValueError as e:
        return str(e)
    except Exception as e:
        return f"发生意外错误: {e}"


def read_file(filename: str) -> str:
    """
    从工作区读取文件的内容。
    以字符串形式返回文件内容。
    """
    try:
        safe_path = _get_safe_path(filename)
        if not safe_path.is_file():
            return f"错误：文件 '{filename}' 未找到或是一个目录。"
        return safe_path.read_text(encoding='utf-8')
    except ValueError as e:
        return str(e)
    except Exception as e:
        return f"读取文件时发生意外错误: {e}"


def create_file(filename: str, content: str) -> str:
    """
    在工作区中创建具有指定内容的新文件。
    如果文件已存在，则会覆盖它。
    """
    try:
        safe_path = _get_safe_path(filename)
        # 确保父目录存在
        safe_path.parent.mkdir(parents=True, exist_ok=True)
        safe_path.write_text(content, encoding='utf-8')
        return f"文件 '{filename}' 已成功创建/更新。"
    except ValueError as e:
        return str(e)
    except Exception as e:
        return f"创建文件时发生意外错误: {e}"


from tree_sitter_language_pack import get_language, get_parser

def _get_ast(filepath: Path) -> (any, bytes):
    """辅助函数：解析文件并返回 AST 和原始内容。"""
    # 目前仅支持 Python
    language = get_language('python')
    parser = get_parser('python')

    content_bytes = filepath.read_bytes()
    tree = parser.parse(content_bytes)
    return tree, content_bytes

def _find_node_recursively(node, criteria):
    """一个辅助函数，用于在 AST 中递归地查找满足条件的节点。"""
    if criteria(node):
        return node
    for child in node.children:
        found_node = _find_node_recursively(child, criteria)
        if found_node:
            return found_node
    return None

def replace_function_definition(filename: str, function_name: str, new_function_code: str) -> str:
    """
    通过递归遍历 AST 来精确地替换一个函数的定义。
    """
    try:
        safe_path = _get_safe_path(filename)
        if not safe_path.is_file():
            return f"错误：文件 '{filename}' 未找到。"

        tree, original_bytes = _get_ast(safe_path)

        def find_function_criteria(node):
            if node.type == 'function_definition':
                name_node = node.child_by_field_name("name")
                if name_node and name_node.text.decode('utf8') == function_name:
                    return True
            return False

        node_to_replace = _find_node_recursively(tree.root_node, find_function_criteria)

        if not node_to_replace:
            return f"错误: 在 '{filename}' 中未找到名为 '{function_name}' 的函数。"

        start_byte = node_to_replace.start_byte
        end_byte = node_to_replace.end_byte

        new_bytes = original_bytes[:start_byte] + new_function_code.encode('utf8') + original_bytes[end_byte:]
        safe_path.write_bytes(new_bytes)

        return f"函数 '{function_name}' 在 '{filename}' 中已成功替换。"
    except Exception as e:
        return f"使用 AST 替换函数时发生意外错误: {e}"

def insert_into_class_body(filename: str, class_name: str, code_to_insert: str) -> str:
    """
    通过递归遍历 AST 在一个类的末尾插入代码。
    """
    try:
        safe_path = _get_safe_path(filename)
        if not safe_path.is_file():
            return f"错误：文件 '{filename}' 未找到。"

        tree, original_bytes = _get_ast(safe_path)

        def find_class_criteria(node):
            if node.type == 'class_definition':
                name_node = node.child_by_field_name("name")
                if name_node and name_node.text.decode('utf8') == class_name:
                    return True
            return False

        class_node = _find_node_recursively(tree.root_node, find_class_criteria)

        if not class_node:
            return f"错误: 在 '{filename}' 中未找到名为 '{class_name}' 的类。"

        body_node = class_node.child_by_field_name("body")
        if not body_node:
            return f"错误: 在 '{filename}' 中找到类 '{class_name}'，但它没有主体。"

        # 确定正确的缩进和插入点
        if body_node.named_child_count > 0:
            last_child = body_node.named_children[-1]
            indentation_level = last_child.start_point[1]
            insertion_point = last_child.end_byte
            # 在最后一个节点后插入一个换行符，以确保格式正确
            code_to_insert = "\n" + code_to_insert
        else: # 如果类是空的 (只有 pass 或 docstring)
            indentation_level = class_node.start_point[1] + 4
            insertion_point = body_node.start_byte + 1
            # 在类主体开头插入一个换行符
            code_to_insert = "\n" + code_to_insert

        indentation = " " * indentation_level
        indented_code_to_insert = "\n".join(indentation + line for line in code_to_insert.splitlines())

        new_bytes = original_bytes[:insertion_point] + indented_code_to_insert.encode('utf8') + original_bytes[insertion_point:]
        safe_path.write_bytes(new_bytes)

        return f"代码已成功插入到类 '{class_name}' 的主体中。"
    except Exception as e:
        return f"使用 AST 插入类主体时发生意外错误: {e}"


def delete_file(filename: str) -> str:
    """
    从工作区删除一个文件。
    """
    try:
        safe_path = _get_safe_path(filename)
        if not safe_path.is_file():
            return f"错误：文件 '{filename}' 未找到或不是一个文件。"

        safe_path.unlink()
        return f"文件 '{filename}' 已成功删除。"
    except ValueError as e:
        return str(e)
    except Exception as e:
        return f"删除文件时发生意外错误: {e}"


def write_to_scratchpad(content: str) -> str:
    """
    将内容追加到工作区的临时便签文件中。
    用于在任务步骤之间传递信息。
    """
    try:
        # 便签文件存储在工作区根目录
        scratchpad_path = WORKSPACE_DIR / ".scratchpad.md"
        with scratchpad_path.open("a", encoding="utf-8") as f:
            f.write(content + "\n")
        return "内容已成功写入便签。"
    except Exception as e:
        return f"写入便签时发生意外错误: {e}"


def read_scratchpad() -> str:
    """
    读取工作区临时便签文件的全部内容。
    """
    try:
        scratchpad_path = WORKSPACE_DIR / ".scratchpad.md"
        if not scratchpad_path.is_file():
            return "便签为空或不存在。"
        return scratchpad_path.read_text(encoding="utf-8")
    except Exception as e:
        return f"读取便签时发生意外错误: {e}"


def run_in_bash(command: str) -> str:
    """
    在工作区目录中运行一个 bash 命令。
    以字符串形式返回命令的 stdout 和 stderr。
    """
    try:
        # 我们在工作区目录内运行命令。
        result = subprocess.run(
            command,
            shell=True,
            cwd=WORKSPACE_DIR,
            capture_output=True,
            text=True,
            check=False  # 对非零退出代码不引发异常
        )
        output = ""
        if result.stdout:
            output += f"STDOUT:\n{result.stdout}\n"
        if result.stderr:
            output += f"STDERR:\n{result.stderr}\n"

        output += f"返回码: {result.returncode}"
        return output
    except Exception as e:
        return f"运行命令时发生意外错误: {e}"

# --- Git 工具函数 ---
import git

def git_status() -> str:
    """获取工作区 Git 仓库的当前状态。"""
    try:
        repo = git.Repo(WORKSPACE_DIR)

        status = repo.git.status()
        return f"Git Status:\n{status}"
    except git.exc.InvalidGitRepositoryError:
        return "错误：工作区不是一个有效的 Git 仓库。"
    except Exception as e:
        return f"获取 Git 状态时发生意外错误: {e}"

def git_diff(filepath: str = None) -> str:
    """
    获取文件或整个仓库的变更。
    如果未提供 filepath，则显示已暂存的变更。
    """
    try:
        repo = git.Repo(WORKSPACE_DIR)

        # 如果提供了文件路径，则显示该文件的 diff（包括未暂存的）
        # 否则，显示已暂存的变更 (HEAD)
        diff_target = [filepath] if filepath else None
        diff = repo.git.diff(diff_target, head=not filepath)

        if not diff:
            return "无变更。"
        return f"Git Diff:\n{diff}"
    except git.exc.InvalidGitRepositoryError:
        return "错误：工作区不是一个有效的 Git 仓库。"
    except Exception as e:
        return f"获取 Git diff 时发生意外错误: {e}"

def git_add(filepath: str) -> str:
    """将指定文件添加到 Git 暂存区。"""
    try:
        # 我们使用 _get_safe_path 来确保文件在工作区内
        safe_path = _get_safe_path(filepath)
        repo = git.Repo(WORKSPACE_DIR)

        repo.git.add(str(safe_path))
        return f"文件 '{filepath}' 已成功添加到暂存区。"
    except ValueError as e:
        return str(e)
    except git.exc.InvalidGitRepositoryError:
        return "错误：工作区不是一个有效的 Git 仓库。"
    except Exception as e:
        return f"Git add 操作失败: {e}"

def git_commit(message: str) -> str:
    """提交所有暂存的变更。"""
    try:
        repo = git.Repo(WORKSPACE_DIR)

        # 配置临时的提交者信息，以防全局未配置
        repo.config_writer().set_value("user", "name", "MiniJules").release()
        repo.config_writer().set_value("user", "email", "minijules@agent.ai").release()

        commit = repo.git.commit(m=message)
        return f"成功提交变更:\n{commit}"
    except git.exc.InvalidGitRepositoryError:
        return "错误：工作区不是一个有效的 Git 仓库。"
    except Exception as e:
        return f"Git commit 操作失败: {e}"