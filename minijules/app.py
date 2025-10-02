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

config_list = load_llm_config()

# 如果配置加载失败，则退出程序
if not config_list:
    exit("无法继续，因为 LLM 配置不正确或缺失。")

# 将加载的配置分配给导入的代理
assign_llm_config(config_list)


# --- 工具注册 ---

# 将我们工具模块中的所有函数注册到 Executor 代理
# 这赋予了 Executor 执行文件操作和版本控制的能力
executor.register_function(
    fn_map={
        # 文件系统工具
        "list_files": tools.list_files,
        "read_file": tools.read_file,
        "create_file": tools.create_file,
        "delete_file": tools.delete_file,
        "replace_code_block": tools.replace_code_block,
        "write_to_scratchpad": tools.write_to_scratchpad,
        "read_scratchpad": tools.read_scratchpad,
        "run_in_bash": tools.run_in_bash,
        # Git 工具
        "git_status": tools.git_status,
        "git_diff": tools.git_diff,
        "git_add": tools.git_add,
        "git_commit": tools.git_commit,
    }
)

# --- 工作流编排 ---
# 旧的 GroupChatManager 已被移除。
# 新的交互式工作流在 main() 函数中直接实现。


import git
from pathlib import Path
import argparse

# --- 主程序入口 ---
def main():
    parser = argparse.ArgumentParser(description="MiniJules: 一个AI开发助手")
    parser.add_argument("task", type=str, help="要执行的主要任务描述。")
    args = parser.parse_args()
    task = args.task

    print(f"--- 接收到任务 ---\n{task}\n" + "="*20)

    # 确保工作区是一个 Git 仓库
    try:
        repo = git.Repo(tools.WORKSPACE_DIR)
        print("Git 仓库已存在。")
    except git.exc.InvalidGitRepositoryError:
        print(f"工作区不是一个 Git 仓库。正在运行 'git init'...")
        repo = git.Repo.init(tools.WORKSPACE_DIR)
        print("Git 仓库已成功初始化。")

    # 1. 索引工作区并检索上下文
    indexing.index_workspace()
    retrieved_context = indexing.retrieve_context(task, n_results=5)
    context_str = "\n\n".join(retrieved_context) if retrieved_context else "无相关代码上下文。"

    # 2. 生成计划
    plan_prompt = f"""
--- 相关代码上下文 ---
{context_str}

--- 任务 ---
基于以上上下文，请为以下任务制定一个分步计划：
{task}
"""
    print("--- 正在生成计划... ---")
    chat_result = user_proxy.initiate_chat(
        planner,
        message=plan_prompt,
        max_turns=1,
        silent=True,
    )
    plan_text = chat_result.summary.strip()

    plan_steps = [step.strip() for step in plan_text.split('\n') if step.strip() and step.strip()[0].isdigit()]

    if not plan_steps:
        print("错误：无法从Planner获取有效的执行计划。Planner的回复是：")
        print(plan_text)
        return

    print("--- 生成的计划 ---")
    for i, step in enumerate(plan_steps):
        print(f"{i+1}. {step}")
    print("="*20)

    # 3. 交互式执行
    for i, step in enumerate(plan_steps):
        print(f"\n--- 执行步骤 {i+1}/{len(plan_steps)} ---")
        print(f"> {step}")

        user_input = input("✅ 按 Enter键 继续, 或输入 'exit' 退出: ")

        if user_input.lower() == 'exit':
            print("用户中止了任务。")
            break

        # 将单个步骤发送给 Executor 执行
        print("--- Executor 正在执行... ---")
        execution_result = user_proxy.initiate_chat(
            executor,
            message=step,
            max_turns=1,
            silent=True,
        )

        result_summary = execution_result.summary.strip()
        print("--- 执行结果 ---")
        print(result_summary)
        print("="*20)

        # 检查执行结果中是否包含明显的错误指示
        if "error" in result_summary.lower() or "错误" in result_summary.lower():
             print("\n检测到错误，任务执行已中止。请检查以上输出。")
             break
    else:
        print("\n--- ✅ 所有计划步骤均已成功完成 ---")

    print("--- 任务流程结束 ---")

if __name__ == "__main__":
    main()