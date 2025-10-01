import autogen
from typing import Dict, List
import json
import os
from pathlib import Path

# 导入我们为代理创建的工具集和 RAG 索引引擎
import minijules.tools as tools
import minijules.indexing as indexing

# --- 代理配置 ---

def load_llm_config():
    """
    从文件或环境变量加载 LLM 配置。
    优先级:
    1. minijules/config.json
    2. OAI_CONFIG_LIST 环境变量
    """
    config_path = Path(__file__).parent / "config.json"

    # 尝试从 config.json 加载
    if config_path.exists():
        try:
            return autogen.config_list_from_json(str(config_path))
        except Exception as e:
            print(f"错误: 无法解析 {config_path}。请确保它是有效的 JSON 格式。错误: {e}")
            return None

    # 尝试从环境变量加载
    config_list_json = os.environ.get("OAI_CONFIG_LIST")
    if config_list_json:
        try:
            return autogen.config_list_from_json(env_or_file=config_list_json)
        except Exception as e:
            print(f"错误: 无法解析 OAI_CONFIG_LIST 环境变量。请确保它是有效的 JSON 字符串。错误: {e}")
            return None

    # 如果两种方法都失败
    print("错误: LLM 配置未找到。")
    print("请按以下方式之一提供配置:")
    print(f"1. 创建一个 'minijules/config.json' 文件 (可以从 'minijules/config.json.template' 复制)。")
    print("2. 或者，设置 OAI_CONFIG_LIST 环境变量。")
    return None

config_list = load_llm_config()

# 如果配置加载失败，则退出程序
if not config_list:
    exit("无法继续，因为 LLM 配置不正确或缺失。")

# LLM 配置字典，用于初始化代理
llm_config = {
    "config_list": config_list,
    "cache_seed": 42,  # 使用缓存以提高效率
    "temperature": 0,
}


# --- 代理定义 ---

# 1. Planner Agent (规划者)
# 这个代理不执行代码。它的唯一工作是接收任务并创建详细的、分步骤的计划。
planner = autogen.ConversableAgent(
    name="Planner",
    system_message="""您是一个专业的项目规划师。您的任务是接收一个高级目标和相关代码上下文，并将其分解成一个清晰、简洁、可执行的步骤列表。
您的计划必须是一个编号列表，每一项都是一个独立的、可操作的指令。
在您的回复中，只包含计划本身，不要有任何额外的解释或客套话。
例如:
1. 读取文件 `src/main.py`。
2. 在第 25 行之后插入新的函数 `def new_feature()`。
3. 运行测试命令 `pytest`。""",
    llm_config=llm_config,
)

# 2. Executor Agent (执行者)
# 这个代理是实际的工作者。它接收单个具体的任务，并使用其工具来完成任务。
executor = autogen.AssistantAgent(
    name="Executor",
    system_message="""您是任务执行者。您将接收一个具体的任务，并使用您可用的工具来完成它。
在完成每个任务后，报告您的结果。如果出现问题，请报告错误。""",
    llm_config=llm_config,
)

# 3. User Proxy Agent (用户代理)
# 这个代理代表真实用户。它发起对话，并可以配置为执行代码或请求人工输入。
user_proxy = autogen.UserProxyAgent(
    name="UserProxy",
    human_input_mode="TERMINATE",  # 在需要时请求人工输入，输入 "exit" 终止
    max_consecutive_auto_reply=10,
    is_termination_msg=lambda x: x.get("content", "").rstrip().endswith("TERMINATE"),
    code_execution_config={
        "work_dir": "workspace", # 指定代码执行的工作目录
        "use_docker": False, # 为简单起见不使用 Docker
    },
)


# --- 工具注册 ---

# 将我们工具模块中的所有函数注册到 Executor 代理
# 这赋予了 Executor 执行文件操作和版本控制的能力
executor.register_function(
    fn_map={
        # 文件系统工具
        "list_files": tools.list_files,
        "read_file": tools.read_file,
        "create_file": tools.create_file,
        "replace_in_file": tools.replace_in_file,
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


import datetime
import git
from pathlib import Path
import argparse

# --- 记忆功能 ---
MEMORY_FILE = "minijules/memory.log"

def record_memory(summary: str):
    """将任务摘要记录到长期记忆日志中。"""
    with open(MEMORY_FILE, "a", encoding="utf-8") as f:
        timestamp = datetime.datetime.now().isoformat()
        f.write(f"--- MEMORY @ {timestamp} ---\n")
        f.write(summary)
        f.write("\n\n")
    print("已将任务摘要记录到长期记忆中。")


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

    # 4. 记录总结
    memory_summary = f"已完成任务: {task}\n执行的计划:\n{plan_text}"
    record_memory(memory_summary)
    print("--- 任务流程结束 ---")

if __name__ == "__main__":
    main()