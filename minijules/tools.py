import os
import subprocess
from pathlib import Path
import git
import json
import re
import logging

# 导入 AutoGen v0.4 相关模块
from autogen_ext.code_executors.local import LocalCommandLineCodeExecutor
from tree_sitter_language_pack import get_language, get_parser
from autogen_ext.models.openai import OpenAIChatCompletionClient

# 导入项目模块

# --- 工具配置常量 ---
GIT_AUTHOR_NAME = "MiniJules"
GIT_AUTHOR_EMAIL = "minijules@agent.ai"

ROOT_DIR = Path(__file__).parent.parent.resolve()
WORKSPACE_DIR = Path(__file__).parent.resolve() / "workspace"
WORKSPACE_DIR.mkdir(exist_ok=True)

logger = logging.getLogger(__name__)

# --- 多语言配置中心 ---
def load_language_config():
    """从 JSON 文件加载多语言配置。"""
    try:
        config_path = Path(__file__).parent / "language_config.json"
        with config_path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logging.error(f"错误: 无法加载或解析 language_config.json: {e}")
        return {}

LANGUAGE_CONFIG = load_language_config()

# --- 辅助函数 ---

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
    parser = get_parser(lang_name)
    content_bytes = filepath.read_bytes()
    tree = parser.parse(content_bytes)
    return tree, content_bytes, lang_config

def _get_node_name(node, node_type, lang_config):
    if lang_config["language"] == 'javascript' and node_type == 'variable_declarator':
        name_node = node.child_by_field_name('name')
        value_node = node.child_by_field_name('value')
        if name_node and value_node and value_node.type == 'arrow_function':
            return name_node.text.decode('utf8')
    elif node_type in lang_config.get("function_node_types", []) or node_type == lang_config.get("class_node_type"):
         name_node = node.child_by_field_name("name")
         if name_node: return name_node.text.decode('utf8')
    elif node_type in ['type_spec', 'struct_item']:
        name_node = node.children[0] if node.children else None
        if name_node: return name_node.text.decode('utf8')
    return None

def _traverse_for_structure(node, lang_config, indent_level=1):
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
                body_node = next((c for c in node.children if 'body' in c.type or 'block' in c.type or 'declaration_list' in c.type or 'field_declaration_list' in c.type), None)
                if not body_node and lang_config['language'] == 'go':
                     struct_type_node = next((c for c in node.children if c.type == 'struct_type'), None)
                     if struct_type_node: body_node = next((c for c in struct_type_node.children if c.type == 'field_declaration_list'), None)
                if body_node:
                    for child in body_node.children:
                        structure_list.extend(_traverse_for_structure(child, lang_config, indent_level + 1))
        return structure_list
    for child in node.children:
        structure_list.extend(_traverse_for_structure(child, lang_config, indent_level))
    return structure_list

# --- Agent 可用工具 ---

def google_search(query: str) -> str:
    """
    使用 DuckDuckGo 搜索引擎执行在线搜索。
    由于它不依赖 Google API，因此不需要 API 密钥。
    返回一个包含搜索结果（标题、链接和摘要）的格式化字符串。
    """
    logger.info(f"正在执行 Google 搜索: {query}")
    try:
        # 使用 duckduckgo_search 库，因为它不需要API密钥
        from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=5))

        if not results:
            return "未找到相关结果。"

        formatted_results = []
        for i, result in enumerate(results):
            formatted_results.append(
                f"{i+1}. **{result['title']}**\n"
                f"   链接: {result['href']}\n"
                f"   摘要: {result['body']}\n"
            )
        return "\n".join(formatted_results)
    except ImportError:
        return "错误: `duckduckgo-search` 库未安装。请使用 `pip install duckduckgo-search` 安装。"
    except Exception as e:
        return f"执行搜索时发生意外错误: {e}"

def view_text_website(url: str) -> str:
    """
    获取指定 URL 的纯文本内容。
    此工具会剥离 HTML 标签、脚本和样式，只返回可读的文本内容。
    """
    logger.info(f"正在访问网站: {url}")
    try:
        import requests
        from bs4 import BeautifulSoup

        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()  # 如果请求失败则抛出异常

        # 使用 BeautifulSoup 从 HTML 中提取文本
        soup = BeautifulSoup(response.text, 'html.parser')

        # 移除脚本和样式元素
        for script_or_style in soup(['script', 'style']):
            script_or_style.decompose()

        # 获取文本并进行清理
        text = soup.get_text()
        lines = (line.strip() for line in text.splitlines())
        chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
        cleaned_text = '\n'.join(chunk for chunk in chunks if chunk)

        return cleaned_text if cleaned_text else "无法从 URL 中提取任何文本内容。"

    except ImportError:
        return "错误: 需要 `requests` 和 `beautifulsoup4` 库。请使用 `pip install requests beautifulsoup4` 安装。"
    except requests.RequestException as e:
        return f"访问 URL 时发生网络错误: {e}"
    except Exception as e:
        return f"查看网站时发生意外错误: {e}"

def read_agents_md() -> str:
    """
    读取项目根目录下的 AGENTS.md 文件（如果存在）。
    该文件用于提供针对特定代码库的指导、规则和提示。
    """
    try:
        # AGENTS.md应该在项目根目录, 而不是在 minijules/workspace
        agents_md_path = ROOT_DIR / "AGENTS.md"
        if agents_md_path.is_file():
            logger.info("正在读取 AGENTS.md 文件...")
            return agents_md_path.read_text(encoding='utf-8')
        else:
            logger.info("AGENTS.md 文件未找到。")
            return "AGENTS.md 文件未找到。将遵循标准操作流程。"
    except Exception as e:
        return f"读取 AGENTS.md 时发生意外错误: {e}"


def detect_project_language() -> str:
    """
    通过分析文件扩展名来检测项目的主要编程语言。
    返回一个标识语言的字符串，如 'python', 'javascript', 'go', 'rust', 或 'unknown'。
    """
    logger.info("正在检测项目语言...")
    extension_counts = {}

    # 映射文件扩展名到我们在 language_config.json 中定义的语言名称
    lang_map = {ext: config['language'] for ext, config in LANGUAGE_CONFIG.items()}

    for file_path in WORKSPACE_DIR.rglob('*'):
        if file_path.is_file():
            ext = file_path.suffix
            if ext in lang_map:
                lang = lang_map[ext]
                extension_counts[lang] = extension_counts.get(lang, 0) + 1

    if not extension_counts:
        logger.warning("在工作区中未找到受支持的语言文件。")
        return "unknown"

    # 找出数量最多的语言
    dominant_language = max(extension_counts, key=extension_counts.get)
    logger.info(f"检测到项目主要语言为: {dominant_language}")
    return dominant_language


def list_project_structure() -> str:
    """
    递归扫描工作区，解析所有支持的文件，并返回所有类、函数和方法的树状结构。
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
                for node in tree.root_node.children:
                    symbols = _traverse_for_structure(node, lang_config)
                    output_lines.extend(symbols)
            except Exception as e:
                output_lines.append(f"  (Error parsing file: {e})")
        result = "\n".join(output_lines)
        return result if len(output_lines) > 1 else "No supported files found."
    except Exception as e:
        return f"Failed to list project structure: {e}"

def list_files(path: str = ".") -> str:
    """列出给定路径下的文件和目录。"""
    try:
        safe_path = _get_safe_path(path)
        if not safe_path.is_dir(): return f"错误：'{path}' 不是一个目录。"
        items = [f"{item.name}/" if item.is_dir() else item.name for item in sorted(list(safe_path.iterdir()))]
        return "\n".join(items) if items else "目录为空。"
    except Exception as e: return f"列出文件时发生意外错误: {e}"

def read_file(filename: str) -> str:
    """读取指定文件的内容。"""
    try:
        safe_path = _get_safe_path(filename)
        if not safe_path.is_file(): return f"错误：文件 '{filename}' 未找到。"
        return safe_path.read_text(encoding='utf-8')
    except Exception as e: return f"读取文件时发生意外错误: {e}"

def create_file_with_block(filepath: str, content: str) -> str:
    """
    创建一个新文件。如果文件已存在，将返回错误。
    """
    try:
        safe_path = _get_safe_path(filepath)
        if safe_path.exists():
            return f"错误: 文件 '{filepath}' 已存在。请使用 'overwrite_file_with_block' 或 'replace_with_git_merge_diff' 进行修改。"
        safe_path.parent.mkdir(parents=True, exist_ok=True)
        safe_path.write_text(content, encoding='utf-8')
        return f"文件 '{filepath}' 已成功创建。"
    except Exception as e:
        return f"创建文件时发生意外错误: {e}"
create_file_with_block.is_dangerous = True

def overwrite_file_with_block(filepath: str, content: str) -> str:
    """
    用新内容完全覆盖一个现有文件。
    """
    try:
        safe_path = _get_safe_path(filepath)
        safe_path.parent.mkdir(parents=True, exist_ok=True)
        safe_path.write_text(content, encoding='utf-8')
        return f"文件 '{filepath}' 已被成功覆盖。"
    except Exception as e:
        return f"覆盖文件时发生意外错误: {e}"
overwrite_file_with_block.is_dangerous = True

def replace_with_git_merge_diff(filepath: str, content: str) -> str:
    """
    对现有文件执行搜索和替换操作。
    使用Git风格的合并冲突标记来指定要查找和替换的内容。
    例如:
    <<<<<<< SEARCH
    要被替换的旧代码
    =======
    替换后的新代码
    >>>>>>> REPLACE
    """
    try:
        safe_path = _get_safe_path(filepath)
        if not safe_path.is_file():
            return f"错误：文件 '{filepath}' 未找到。"

        original_content = safe_path.read_text(encoding='utf-8')

        # 解析搜索和替换块
        match = re.search(r'<<<<<<< SEARCH\n(.*?)\n=======\n(.*?)\n>>>>>>> REPLACE', content, re.DOTALL)
        if not match:
            return "错误: 输入内容未使用正确的 'SEARCH/REPLACE' 格式。"

        search_block = match.group(1)
        replace_block = match.group(2)

        # 使用 re.sub 来确保只替换一次，并处理可能存在的特殊字符
        new_content, num_replacements = re.subn(re.escape(search_block), replace_block, original_content, count=1)

        if num_replacements == 0:
            return f"错误: 'SEARCH' 块在文件 '{filepath}' 中未找到。"

        safe_path.write_text(new_content, encoding='utf-8')

        return f"文件 '{filepath}' 已成功更新。"
    except Exception as e:
        return f"更新文件时发生意外错误: {e}"
replace_with_git_merge_diff.is_dangerous = True

def delete_file(filename: str) -> str:
    """删除一个文件。"""
    try:
        safe_path = _get_safe_path(filename)
        if not safe_path.is_file(): return f"错误：文件 '{filename}' 未找到。"
        safe_path.unlink()
        return f"文件 '{filename}' 已成功删除。"
    except Exception as e: return f"删除文件时发生意外错误: {e}"
delete_file.is_dangerous = True

def rename_file(filepath: str, new_filepath: str) -> str:
    """重命名或移动一个文件或目录。"""
    try:
        safe_from_path = _get_safe_path(filepath)
        safe_to_path = _get_safe_path(new_filepath)

        if not safe_from_path.exists():
            return f"错误: 源路径 '{filepath}' 不存在。"
        if safe_to_path.exists():
            return f"错误: 目标路径 '{new_filepath}' 已存在。"

        safe_to_path.parent.mkdir(parents=True, exist_ok=True)
        safe_from_path.rename(safe_to_path)
        return f"路径已成功从 '{filepath}' 重命名为 '{new_filepath}'。"
    except Exception as e:
        return f"重命名文件时发生意外错误: {e}"
rename_file.is_dangerous = True

def grep(pattern: str) -> str:
    """在工作区中递归搜索文件内容。"""
    try:
        # 使用 -r (递归) 和 -n (行号) 标志进行搜索
        # 使用 shlex.quote 来安全地处理用户输入的模式
        import shlex
        quoted_pattern = shlex.quote(pattern)
        command = f"grep -rn {quoted_pattern} ."

        result = subprocess.run(command, shell=True, cwd=WORKSPACE_DIR, capture_output=True, text=True, check=False)

        # grep 在未找到匹配项时返回码为 1，这不应被视为一个程序错误
        if result.returncode > 1:
            return f"Grep 命令执行出错 (返回码: {result.returncode}):\n{result.stderr}"

        output = result.stdout if result.stdout else "未找到匹配项。"
        return output

    except Exception as e:
        return f"执行 grep 时发生意外错误: {e}"

def run_in_bash_session(command: str) -> str:
    """在 bash 会话中运行命令。"""
    try:
        result = subprocess.run(command, shell=True, cwd=WORKSPACE_DIR, capture_output=True, text=True, check=False)
        output = f"STDOUT:\n{result.stdout}\n" if result.stdout else ""
        output += f"STDERR:\n{result.stderr}\n" if result.stderr else ""
        output += f"返回码: {result.returncode}"
        return output
    except Exception as e: return f"运行命令时发生意外错误: {e}"
run_in_bash_session.is_dangerous = True

def apply_patch(filename: str, patch_content: str) -> str:
    """应用一个补丁。"""
    try:
        safe_path = _get_safe_path(filename)
        if not safe_path.is_file(): return f"错误: 文件 '{filename}' 不存在。"
        result = subprocess.run(["patch", str(safe_path)], input=patch_content, text=True, capture_output=True, cwd=WORKSPACE_DIR, check=False)
        if result.returncode != 0:
            return f"应用补丁失败 (返回码: {result.returncode}):\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        return f"补丁已成功应用于 '{filename}'。"
    except Exception as e: return f"应用补丁时发生意外错误: {e}"
apply_patch.is_dangerous = True

def git_status() -> str:
    """获取 git 状态。"""
    try:
        repo = git.Repo(WORKSPACE_DIR)
        return f"Git Status:\n{repo.git.status()}"
    except Exception as e: return f"获取 Git 状态时发生意外错误: {e}"

def git_diff(filepath: str = None) -> str:
    """获取 git diff。"""
    try:
        repo = git.Repo(WORKSPACE_DIR)
        diff = repo.git.diff(filepath)
        if not diff: diff = repo.git.diff('--staged', filepath)
        return f"Git Diff:\n{diff}" if diff else "无变更。"
    except Exception as e: return f"获取 Git diff 时发生意外错误: {e}"

def git_add(filepath: str) -> str:
    """git add 一个文件。"""
    try:
        repo = git.Repo(WORKSPACE_DIR)
        repo.git.add(str(_get_safe_path(filepath)))
        return f"文件 '{filepath}' 已成功添加到暂存区。"
    except Exception as e: return f"Git add 操作失败: {e}"
git_add.is_dangerous = True

def git_commit(message: str) -> str:
    """git commit。"""
    try:
        repo = git.Repo(WORKSPACE_DIR)
        repo.config_writer().set_value("user", "name", GIT_AUTHOR_NAME).release()
        repo.config_writer().set_value("user", "email", GIT_AUTHOR_EMAIL).release()
        return f"成功提交变更:\n{repo.git.commit(m=message)}"
    except Exception as e: return f"Git commit 操作失败: {e}"
git_commit.is_dangerous = True

def git_create_branch(branch_name: str) -> str:
    """创建 git 分支。"""
    try:
        repo = git.Repo(WORKSPACE_DIR)
        new_branch = repo.create_head(branch_name)
        new_branch.checkout()
        return f"已成功创建并切换到新分支: '{branch_name}'。"
    except Exception as e: return f"创建 Git 分支时发生意外错误: {e}"
git_create_branch.is_dangerous = True


def restore_file(filepath: str) -> str:
    """将指定文件恢复到任务开始时的状态。"""
    try:
        safe_path = _get_safe_path(filepath)
        repo = git.Repo(WORKSPACE_DIR)

        # 检查标签是否存在
        tag_name = "minijules-initial-state"
        if tag_name not in repo.tags:
            return f"错误: 未找到初始状态标签 '{tag_name}'。无法执行恢复。"

        repo.git.checkout(tag_name, '--', safe_path)
        return f"文件 '{filepath}' 已成功恢复到初始状态。"
    except Exception as e:
        return f"恢复文件时发生意外错误: {e}"
restore_file.is_dangerous = True


def reset_all() -> str:
    """将整个工作区恢复到任务开始时的状态。"""
    try:
        repo = git.Repo(WORKSPACE_DIR)

        # 检查标签是否存在
        tag_name = "minijules-initial-state"
        if tag_name not in repo.tags:
            return f"错误: 未找到初始状态标签 '{tag_name}'。无法执行重置。"

        repo.git.reset('--hard', tag_name)
        # 清理所有未被跟踪的文件和目录
        repo.git.clean('-fdx')

        return "整个工作区已成功重置到初始状态。"
    except Exception as e:
        return f"重置工作区时发生意外错误: {e}"
reset_all.is_dangerous = True


def _parse_pytest_output(output: str) -> list[dict[str, any]]:
    """
    解析 pytest 的输出，提取失败和错误信息。
    """
    failures = []
    # Regex to find the detailed failure/error blocks. The lookahead group is made non-capturing.
    failure_blocks = re.findall(r"_{10,}\s(.*?)\s_{10,}([\s\S]*?)(?=(?:\n_{10,}|\n={10,}))", output)

    for test_name, block_content in failure_blocks:
        # Regex to find file path, line number, and error type in the traceback
        match = re.search(r"(\S+\.py):(\d+):\s(\w+Error)", block_content)
        if match:
            filepath, lineno, error_type = match.groups()

            # Extract the summary line for a more descriptive error message. Handles cases where pytest omits the error type for brevity (e.g., AssertionError).
            summary_match = re.search(r"E\s+(?:\w+Error: )?(.*)", block_content, re.MULTILINE)
            error_message = summary_match.group(1).strip() if summary_match else "No specific error message found."

            failures.append({
                "test_name": test_name.strip(),
                "filepath": filepath,
                "line_number": int(lineno),
                "error_type": error_type,
                "error_message": error_message,
                "full_traceback": block_content.strip()
            })

    return failures


async def _generate_fix_patch(
    failure_details: dict,
    file_content: str,
    client: OpenAIChatCompletionClient
) -> str:
    """
    使用 LLM 生成一个用于修复代码的补丁。
    """
    logger.info("开始生成修复补丁...")

    system_prompt = """
您是一位专家级的软件调试工程师。您的任务是根据提供的 pytest 错误信息和完整的源文件内容，生成一个统一差异格式（unified diff）的补丁来修复这个错误。

**规则:**
1.  **分析上下文:** 仔细分析 `pytest` 的完整追溯信息，理解错误的根本原因。
2.  **定位代码:** 在提供的源文件内容中找到需要修改的具体代码行。
3.  **生成补丁:** 创建一个正确的、可以被 `patch` 命令直接应用的修复方案。
4.  **输出格式:** 您的输出**必须**仅包含补丁内容，不需要任何解释、代码块标记（```diff）或任何其他多余的文字。补丁必须以 `--- a/` 和 `+++ b/` 开头。

**示例输入上下文:**
-   **错误信息:** `AssertionError: assert 2 == 1`
-   **文件路径:** `tests/test_math.py`
-   **文件内容:**
    ```python
    def test_addition():
        assert 1 + 1 == 1
    ```

**示例输出 (您的唯一输出):**
```diff
--- a/tests/test_math.py
+++ b/tests/test_math.py
@@ -1,2 +1,2 @@
 def test_addition():
-    assert 1 + 1 == 1
+    assert 1 + 1 == 2

```
"""

    user_prompt = f"""
请为以下错误生成一个修复补丁：

**失败的测试:** {failure_details['test_name']}
**文件路径:** {failure_details['filepath']}
**错误类型:** {failure_details['error_type']}
**错误信息:** {failure_details['error_message']}

**完整的 Pytest 追溯信息:**
```
{failure_details['full_traceback']}
```

**完整的源文件内容 (`{failure_details['filepath']}`):**
```
{file_content}
```

请严格按照规则，只输出可以直接应用的 `diff` 格式补丁。
"""

    try:
        from autogen_core.models import SystemMessage, UserMessage
        response = await client.create(
            messages=[
                SystemMessage(content=system_prompt),
                UserMessage(content=user_prompt),
            ]
        )
        patch_content = response.content
        if not isinstance(patch_content, str):
            patch_content = str(patch_content)

        logger.info(f"成功生成修复补丁:\n{patch_content}")
        return patch_content
    except Exception as e:
        logger.error(f"生成补丁时发生错误: {e}")
        return ""


async def run_tests_and_debug(
    test_command: str,
    client: OpenAIChatCompletionClient,
    max_retries: int = 3
) -> str:
    """
    [高级工具] 运行测试命令，如果失败，则尝试自动调试和修复。

    这个工具会执行以下操作：
    1. 运行指定的 `test_command` (例如, `python3 -m pytest`, `npm test`)。
    2. 如果测试通过，则报告成功。
    3. 如果测试失败，它将启动一个循环（最多 `max_retries` 次）：
       a. 解析 pytest 的错误日志以找出失败的文件和错误。
       b. 读取相关的代码。
       c. 使用 LLM 生成一个修复补丁。
       d. 应用补丁。
       e. 重新运行测试。
    4. 返回最终的测试结果或调试过程的总结。
    """
    logger.info("开始执行测试和调试循环...")

    for attempt in range(max_retries + 1):
        logger.info(f"第 {attempt + 1}/{max_retries + 1} 次尝试使用命令 '{test_command}' 运行测试...")

        test_result = run_in_bash_session(test_command)

        if " passed" in test_result and "failed" not in test_result and "error" not in test_result:
            success_message = f"所有测试在第 {attempt + 1} 次尝试中成功通过。\n\n{test_result}"
            logger.info(success_message)
            return success_message

        if attempt >= max_retries:
            failure_message = f"在达到 {max_retries + 1} 次尝试后，测试仍然失败。\n\n最终测试结果:\n{test_result}"
            logger.error(failure_message)
            return failure_message

        # 解析错误
        failures = _parse_pytest_output(test_result)
        if not failures:
            logger.warning("测试失败，但无法解析出具体的错误信息。将返回原始测试输出。")
            return f"测试失败，且无法解析错误。\n\n{test_result}"

        logger.info(f"成功解析出 {len(failures)} 个测试失败:")

        # --- 代码定位与上下文收集 ---
        # 目前，我们只关注第一个失败来进行修复
        first_failure = failures[0]
        logger.info(f"正在为第一个失败的测试 '{first_failure['test_name']}' 收集上下文...")

        try:
            # 使用内部函数 _get_safe_path 和 read_text 来读取文件
            file_path = _get_safe_path(first_failure['filepath'])
            original_content = file_path.read_text(encoding='utf-8')
            logger.info(f"已成功读取文件 '{first_failure['filepath']}' 的内容。")

            # --- LLM 生成修复 ---
            patch_content = await _generate_fix_patch(
                failure_details=first_failure,
                file_content=original_content,
                client=client
            )

            if not patch_content:
                logger.error("LLM 未能生成修复补丁。终止调试循环。")
                return f"测试失败，且LLM未能生成修复方案。\n\n{test_result}"

            # --- 应用补丁 ---
            logger.info(f"正在向 {first_failure['filepath']} 应用修复补丁...")
            apply_result = apply_patch(first_failure['filepath'], patch_content)

            if "应用补丁失败" in apply_result:
                logger.error(f"应用补丁失败: {apply_result}。正在恢复文件...")
                # 在应用补丁失败时，恢复文件以避免代码库处于损坏状态
                overwrite_file_with_block(first_failure['filepath'], original_content)
                logger.info(f"文件 '{first_failure['filepath']}' 已恢复到补丁应用前的状态。")
                return f"测试失败，且生成的补丁无法被应用。\n\n{apply_result}"

            logger.info("补丁已成功应用。进入下一轮测试验证...")

        except Exception as e:
            logger.error(f"为失败的测试收集上下文时发生错误: {e}")
            return f"测试失败，并且在读取文件 {first_failure['filepath']} 时出错。"

    return "调试循环因未知原因完成。"
run_tests_and_debug.is_dangerous = True