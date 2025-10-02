import autogen
from typing import Dict, List
import json
import os
from pathlib import Path
from dotenv import load_dotenv

# 导入我们为代理创建的工具集和 RAG 索引引擎
import minijules.tools as tools
import minijules.indexing as indexing
from minijules.agents import planner, executor, user_proxy, assign_llm_config

# --- 代理配置 ---

def load_llm_config():
    """
    从 .env 文件或环境变量加载 LLM 配置。
    """
    # 加载 .env 文件中的环境变量 (如果存在)
    load_dotenv()

    # 从环境变量 OAI_CONFIG_LIST 中获取配置
    config_list_json = os.environ.get("OAI_CONFIG_LIST")
    if config_list_json:
        try:
            # 使用 autogen 从 JSON 字符串解析配置列表
            return autogen.config_list_from_json(env_or_file=config_list_json)
        except Exception as e:
            print(f"错误: 无法解析 OAI_CONFIG_LIST 环境变量。请确保它是有效的 JSON 字符串。错误: {e}")
            return None

    # 如果环境变量未设置
    print("错误: LLM 配置未找到。")
    print("请按以下方式提供配置:")
    print("1. 在项目根目录创建一个 `.env` 文件。")
    print("2. 在 `.env` 文件中，定义 `OAI_CONFIG_LIST` 环境变量。")
    print("   (您可以从 `.env.template` 文件复制格式)")
    return None

# --- 工作流编排 ---
# 旧的 GroupChatManager 已被移除。
# 新的交互式工作流在 main() 函数中直接实现。


import git
from pathlib import Path
import argparse

# --- 主程序入口 ---
def main():
    # 0. 加载并分配配置
    config_list = load_llm_config()
    if not config_list:
        return  # 如果配置加载失败，则正常退出
    assign_llm_config(config_list)

    # 1. 设置工具字典，用于动态调用
    tool_map = {
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
    }

    parser = argparse.ArgumentParser(description="MiniJules: 一个AI开发助手")
    parser.add_argument("task", type=str, help="要执行的主要任务描述。")
    args = parser.parse_args()
    task = args.task

    print(f"--- 接收到任务 ---\n{task}\n" + "="*20)

    # 2. 索引工作区并检索上下文
    indexing.index_workspace()
    retrieved_context = indexing.retrieve_context(task, n_results=5)
    context_str = "\n\n".join(retrieved_context) if retrieved_context else "无相关代码上下文。"

    # 3. 生成结构化计划
    plan_prompt = f"""
--- 相关代码上下文 ---
{context_str}

--- 任务 ---
基于以上上下文，请为以下任务制定一个分步计划：
{task}
"""
    print("--- 正在生成计划... ---")
    chat_result = user_proxy.initiate_chat(planner, message=plan_prompt, max_turns=1, silent=True)
    plan_text = chat_result.summary.strip()

    # 4. 解析 JSON 计划
    try:
        # 清理可能存在于回复前后的代码块标记
        if plan_text.startswith("```json"):
            plan_text = plan_text[7:]
        if plan_text.endswith("```"):
            plan_text = plan_text[:-3]

        plan_steps = json.loads(plan_text)
        if not isinstance(plan_steps, list):
            raise json.JSONDecodeError("顶层结构不是一个列表", plan_text, 0)

    except json.JSONDecodeError as e:
        print(f"错误：无法从 Planner 获取有效的 JSON 计划。错误: {e}")
        print("Planner 的原始回复是:")
        print(plan_text)
        return

    print("--- 生成的计划 ---")
    for i, step in enumerate(plan_steps):
        print(f"{i+1}. 工具: {step.get('tool_name')}, 参数: {step.get('parameters')}")
    print("="*20)

    # 5. 交互式地动态执行计划
    for i, step in enumerate(plan_steps):
        tool_name = step.get("tool_name")
        parameters = step.get("parameters", {})

        print(f"\n--- 执行步骤 {i+1}/{len(plan_steps)} ---")
        print(f"> 工具: {tool_name}")
        print(f"> 参数: {parameters}")

        user_input = input("✅ 按 Enter键 继续, 或输入 'exit' 退出: ")
        if user_input.lower() == 'exit':
            print("用户中止了任务。")
            break

        # 动态查找并执行工具
        if tool_name in tool_map:
            tool_function = tool_map[tool_name]
            try:
                print(f"--- 正在执行 {tool_name}... ---")
                result = tool_function(**parameters)
                print("--- 执行结果 ---")
                print(result)
                print("="*20)
            except TypeError as e:
                print(f"错误：传递给工具 '{tool_name}' 的参数不正确。错误: {e}")
                break
            except Exception as e:
                print(f"执行工具 '{tool_name}' 时发生意外错误: {e}")
                break
        else:
            print(f"错误：未知的工具名称 '{tool_name}'。任务中止。")
            break
    else:
        print("\n--- ✅ 所有计划步骤均已成功完成 ---")

    print("--- 任务流程结束 ---")

if __name__ == "__main__":
    main()