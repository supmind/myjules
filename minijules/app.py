import autogen
from typing import Dict, List
import json
import os
from pathlib import Path
from dotenv import load_dotenv
import argparse

# 导入项目模块
import minijules.tools as tools
import minijules.indexing as indexing
from minijules.agents import planner, user_proxy, assign_llm_config
from minijules.result import ToolExecutionResult

# --- 配置加载 ---
def load_llm_config():
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

# --- 智能编排器 ---
class Orchestrator:
    def __init__(self, task: str):
        self.task = task
        self.plan = []
        self.max_replans = 3
        self.tool_map = self._get_tool_map()
        self.last_failed_plan = None
        self.last_error_message = None

    def _get_tool_map(self) -> Dict:
        return {
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
        }

    def _determine_language(self) -> str:
        # 这是一个简单的启发式算法。在更复杂的场景中，可以分析工作区中的文件类型。
        # TODO: 实现更智能的语言检测。
        return "py"

    def _generate_plan(self, context: str, failed_plan: List = None, error_message: str = None):
        prompt = f"---\n相关代码上下文:\n{context}\n\n---\n原始任务:\n{self.task}\n"
        if failed_plan and error_message:
            prompt += f"\n---\n失败的计划:\n{json.dumps(failed_plan, indent=2, ensure_ascii=False)}\n\n---\n错误信息:\n{error_message}\n\n---\n指令:\n以上计划失败。请分析错误并生成一个修正后的全新完整计划。"
        else:
            prompt += "\n--- 指令 ---\n请为以上任务制定一个分步计划。\n"

        print("--- 正在请求 Planner 生成计划... ---")
        chat_result = user_proxy.initiate_chat(planner, message=prompt, max_turns=1, silent=True)
        plan_text = chat_result.summary.strip()
        try:
            if plan_text.startswith("```json"): plan_text = plan_text[7:-3].strip()
            self.plan = json.loads(plan_text)
            print("--- 成功生成新计划 ---")
            for i, step in enumerate(self.plan):
                print(f"{i+1}. 工具: {step.get('tool_name')}, 参数: {step.get('parameters')}")
            return True
        except json.JSONDecodeError:
            print("错误：无法从 Planner 获取有效的 JSON 计划。原始回复:", plan_text)
            self.plan = []
            return False

    def run(self):
        print(f"--- 接收到任务 ---\n{self.task}\n" + "="*20)
        indexing.index_workspace()
        context = "\n\n".join(indexing.retrieve_context(self.task, n_results=5) or ["无相关代码上下文。"])
        if not self._generate_plan(context):
            print("任务因初始规划失败而中止。"); return

        replan_count = 0
        while replan_count <= self.max_replans:
            execution_successful = self._execute_plan()
            if execution_successful:
                print("\n--- ✅ 所有计划步骤均已成功完成 ---"); break

            replan_count += 1
            if replan_count > self.max_replans:
                print("已达到最大重规划次数，任务中止。"); break

            print(f"\n--- 侦测到错误，正在尝试重规划 ({replan_count}/{self.max_replans}) ---")
            if not self._generate_plan(context, self.last_failed_plan, self.last_error_message):
                print("任务因重规划失败而中止。"); break

        print("--- 任务流程结束 ---")

    def _execute_plan(self) -> bool:
        for i, step in enumerate(self.plan):
            tool_name = step.get("tool_name")
            parameters = step.get("parameters", {})

            if tool_name == "run_tests_and_parse_report" and "language" not in parameters:
                parameters["language"] = self._determine_language()

            print(f"\n--- 执行步骤 {i+1}/{len(self.plan)} ---\n> 工具: {tool_name}\n> 参数: {json.dumps(parameters, indent=2, ensure_ascii=False)}")
            if input("✅ 按 Enter键 继续, 或输入 'exit' 退出: ").lower() == 'exit':
                print("用户中止了任务."); return False

            tool_function = self.tool_map.get(tool_name)
            if not tool_function:
                self.last_failed_plan, self.last_error_message = self.plan, f"错误：工具 '{tool_name}' 不存在。"
                print(self.last_error_message); return False
            try:
                exec_result: ToolExecutionResult = tool_function(**parameters)
            except Exception as e:
                exec_result = ToolExecutionResult(success=False, result=f"执行工具 '{tool_name}' 时发生意外的 Python 异常: {e}")

            print(f"--- 执行结果 ---\n{exec_result.result}\n====================")
            if not exec_result.success:
                self.last_failed_plan, self.last_error_message = self.plan, exec_result.result
                return False
        return True

def main():
    config_list = load_llm_config()
    if not config_list: return
    assign_llm_config(config_list)

    parser = argparse.ArgumentParser(description="MiniJules: 一个AI开发助手")
    parser.add_argument("task", type=str, help="要执行的主要任务描述。")
    args = parser.parse_args()

    orchestrator = Orchestrator(task=args.task)
    orchestrator.run()

if __name__ == "__main__":
    main()