import autogen
from typing import Dict, List, Any
import json
import os
from pathlib import Path
from dotenv import load_dotenv
import argparse
from collections import Counter

# 导入项目模块
import minijules.tools as tools
import minijules.indexing as indexing
from minijules.agents import core_agent, user_proxy, assign_llm_config
from minijules.result import ToolExecutionResult

# --- 配置加载 ---
def load_llm_config():
    """从 .env 文件或环境变量加载 LLM 配置。"""
    load_dotenv()
    config_list_json = os.environ.get("OAI_CONFIG_LIST")
    if config_list_json:
        try:
            return autogen.config_list_from_json(env_or_file=config_list_json)
        except Exception as e:
            print(f"错误: 无法解析 OAI_CONFIG_LIST 环境变量。错误: {e}")
            return None
    print("错误: LLM 配置未找到。请参考 .env.template 设置您的配置。")
    return None

# --- 新的主应用 ---
class JulesApp:
    """
    MiniJules 主应用程序类。
    该类负责编排整个 "观察-思考-行动" 循环，模拟 Jules 的工作流程。
    它管理与 CoreAgent 的交互、工具的执行、工作历史的维护以及记忆的利用。
    """
    def __init__(self, task: str, auto_mode: bool = False):
        """
        初始化 JulesApp。

        Args:
            task (str): 要执行的最高级别任务描述。
            auto_mode (bool): 是否启用自主模式，无需手动确认每一步。
        """
        self.task = task
        self.auto_mode = auto_mode
        self.work_history: List[str] = []
        self.max_steps = 30
        self.tool_map = self._get_tool_map()
        self.project_language = self._detect_language()
        print(f"--- 自动检测到项目主要语言为: {self.project_language} ---")


    def _get_tool_map(self) -> Dict:
        """返回所有可用工具的名称到函数的映射。"""
        return {
            "list_project_structure": tools.list_project_structure,
            "list_files": tools.list_files,
            "read_file": tools.read_file,
            "create_file": tools.create_file,
            "delete_file": tools.delete_file,
            "write_to_scratchpad": tools.write_to_scratchpad,
            "read_scratchpad": tools.read_scratchpad,
            "run_in_bash": tools.run_in_bash,
            "replace_function_definition": tools.replace_function_definition,
            "insert_into_class_body": tools.insert_into_class_body,
            "git_status": tools.git_status,
            "git_diff": tools.git_diff,
            "git_add": tools.git_add,
            "git_commit": tools.git_commit,
            "git_create_branch": tools.git_create_branch,
            "run_tests_and_parse_report": tools.run_tests_and_parse_report,
            "manage_dependency": tools.manage_dependency,
            "retrieve_code_context": tools.retrieve_code_context,
            "task_complete": self._task_complete,
        }

    def _detect_language(self) -> str:
        """
        通过扫描工作区中的文件扩展名来自动检测项目的主要语言。
        """
        language_map = { ext: data['language'] for ext, data in tools.LANGUAGE_CONFIG.items() }
        file_extensions = [f.suffix for f in tools.WORKSPACE_DIR.rglob('*') if f.is_file() and f.suffix in language_map]
        if not file_extensions: return 'py'
        most_common_ext = Counter(file_extensions).most_common(1)[0][0]
        return language_map[most_common_ext]

    def _construct_prompt(self, memory_context: str) -> str:
        """构建将发送给 CoreAgent 的提示, 包含最终目标、历史经验和工作历史。"""
        history_section = "\n".join(self.work_history) if self.work_history else "还没有任何动作被执行。"
        memory_section = memory_context if memory_context else "无相关历史经验。"
        return f"""
### **最终目标**
{self.task}

### **相关历史经验**
---
{memory_section}
---

### **工作历史**
---
{history_section}
---
"""

    def _execute_tool(self, tool_name: str, parameters: Dict) -> ToolExecutionResult:
        """
        执行指定的工具并返回结果。
        """
        if tool_name not in self.tool_map:
            return ToolExecutionResult(success=False, result=f"错误: 工具 '{tool_name}' 不存在。")

        tool_function = self.tool_map[tool_name]
        try:
            if tool_name == "run_tests_and_parse_report" and "language" not in parameters:
                parameters["language"] = self.project_language
            return tool_function(**parameters)
        except Exception as e:
            return ToolExecutionResult(success=False, result=f"执行工具 '{tool_name}' 时发生意外的 Python 异常: {e}")

    def _task_complete(self, summary: str) -> ToolExecutionResult:
        """处理任务完成信号，并自动获取代码变更，将任务经验保存到记忆库。"""
        print("--- 正在保存任务经验到记忆库... ---")
        final_diff_result = tools.git_diff()
        final_diff = final_diff_result.result if final_diff_result.success else "获取代码变更失败。"
        full_summary = f"原始任务: {self.task}\n工作总结: {summary}\n\n工作历史:\n" + "\n".join(self.work_history)
        indexing.save_memory(task_summary=full_summary, final_code_diff=final_diff)
        return ToolExecutionResult(success=True, result=f"任务已成功完成，并已存入记忆库: {summary}")

    def run(self):
        """运行主应用循环。"""
        print(f"--- 接收到任务 ---\n{self.task}\n" + "="*20)

        print("--- 正在索引工作区... ---")
        indexing.index_workspace()

        print("--- 正在检索相关历史经验... ---")
        retrieved_memories = indexing.retrieve_memory(self.task, n_results=1)
        memory_context = "\n\n".join(retrieved_memories) if retrieved_memories else ""
        if memory_context:
            print("> 发现相关历史经验。")

        for step in range(self.max_steps):
            print(f"\n--- 思考循环: 第 {step + 1}/{self.max_steps} 步 ---")

            prompt = self._construct_prompt(memory_context)

            chat_result = user_proxy.initiate_chat(core_agent, message=prompt, max_turns=1, silent=False)
            agent_reply = chat_result.summary.strip()

            try:
                if agent_reply.startswith("```json"): agent_reply = agent_reply[7:-3].strip()
                action = json.loads(agent_reply)
                tool_name = action.get("tool_name")
                parameters = action.get("parameters", {})
            except json.JSONDecodeError:
                print(f"错误: CoreAgent 返回了无效的JSON。正在将此错误反馈给代理。")
                self.work_history.append(f"动作: 无\n结果: 错误 - 你上一次的回复不是一个有效的JSON对象。请严格遵循格式要求。")
                continue

            if not tool_name:
                 print(f"错误: CoreAgent 返回的JSON中缺少 'tool_name'。")
                 self.work_history.append(f"动作: 无\n结果: 错误 - 你上一次的回复缺少 'tool_name'。")
                 continue

            print(f"> 下一步动作: {tool_name}")
            print(f"> 参数:\n{json.dumps(parameters, indent=2, ensure_ascii=False)}")

            if not self.auto_mode:
                if input("✅ 按 Enter键 继续, 或输入 'exit' 退出: ").lower() == 'exit':
                    print("用户中止了任务。"); break

            exec_result = self._execute_tool(tool_name, parameters)
            print(f"--- 结果 ---\n{exec_result.result}\n" + "="*20)

            history_entry = f"动作: {tool_name} with params {json.dumps(parameters, ensure_ascii=False)}\n结果: [{'成功' if exec_result.success else '失败'}] {exec_result.result}"
            self.work_history.append(history_entry)

            if tool_name == "task_complete":
                print("\n--- ✅ CoreAgent 已确认任务完成 ---")
                break
        else:
            print("\n--- ⚠️ 已达到最大步数限制，任务中止 ---")

        print("\n--- 任务流程结束 ---")


def main():
    """程序主入口，负责解析命令行参数并启动应用。"""
    config_list = load_llm_config()
    if not config_list: return
    assign_llm_config(config_list)

    parser = argparse.ArgumentParser(
        description="MiniJules: 一个基于 '观察-思考-行动' 循环的AI开发助手。",
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("task", type=str, help="要执行的主要任务描述。")
    parser.add_argument("--auto", action="store_true", help="启用自主模式，无需手动确认每一步。")
    args = parser.parse_args()

    app = JulesApp(task=args.task, auto_mode=args.auto)
    app.run()

if __name__ == "__main__":
    main()