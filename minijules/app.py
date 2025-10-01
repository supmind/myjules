import autogen
from typing import Dict, List

# 导入我们为代理创建的工具集和 RAG 索引引擎
import minijules.tools as tools
import minijules.indexing as indexing

# --- 代理配置 ---

# IMPORTANT: 在实际应用中，这里需要配置语言模型。
# 您可以从环境变量或 JSON 文件加载配置。
# 例如: config_list = autogen.config_list_from_json("OAI_CONFIG_LIST")
# 为简单起见，我们使用一个占位符配置，这在没有真实LLM的情况下无法运行，
# 但它清晰地展示了结构。
config_list = [
    {
        "model": "gpt-4",
        # "api_key": "sk-...", # 在实际使用中需要提供
    }
]

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
    system_message="""您是一个专业的项目规划师。您的任务是接收一个高级目标，并将其分解成一个清晰、简洁、可执行的步骤列表。
请勿自己执行任何步骤。只需创建计划，并以 'PLAN:' 开头，用编号列表的形式输出计划。""",
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
# 这赋予了 Executor 执行 list_files, read_file, create_file, run_in_bash 的能力
executor.register_function(
    fn_map={
        "list_files": tools.list_files,
        "read_file": tools.read_file,
        "create_file": tools.create_file,
        "run_in_bash": tools.run_in_bash,
    }
)

# --- 工作流编排 (GroupChat) ---

# 定义一个 GroupChat，包含我们所有的代理
agents = [user_proxy, planner, executor]
group_chat = autogen.GroupChat(
    agents=agents,
    messages=[],
    max_round=15 # 设置最大对话轮次
)

# 创建一个 GroupChatManager 来管理代理之间的对话
manager = autogen.GroupChatManager(
    groupchat=group_chat,
    llm_config=llm_config,
    # 定义发言者转换逻辑
    # 在这里，我们希望 UserProxy 发起，然后 Planner 介入，最后 Executor 执行
    # 这是一个简化的轮换逻辑，可以通过自定义函数实现更复杂的控制
    speaker_selection_method="auto"
)


import datetime

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
if __name__ == "__main__":
    # 步骤 1: 索引整个工作区以更新 RAG 数据库
    indexing.index_workspace()

    # 步骤 2: 定义用户的原始任务
    task = """
    请修改 'utils.js' 文件中的 'greet' 函数。
    新的实现应该返回一个格式化的字符串 "Hello, [name]!"，而不是直接打印到控制台。
    """
    print(f"--- 原始任务 ---\n{task}\n" + "="*20)

    # 步骤 3: 使用 RAG 检索与任务相关的上下文
    retrieved_context = indexing.retrieve_context(task, n_results=3)

    # 步骤 4: 构建一个“增强版”的提示
    context_str = "\n\n".join(retrieved_context) if retrieved_context else "无相关代码上下文。"
    enhanced_task = f"""
--- 相关代码上下文 ---
{context_str}

--- 任务 ---
基于以上上下文，请完成以下任务：
{task}
"""

    print(f"--- 增强后的任务 ---\n{enhanced_task}\n" + "="*20)

    # 步骤 5: 使用增强后的任务启动代理工作流
    user_proxy.initiate_chat(
        manager,
        message=enhanced_task
    )

    # 步骤 6: 任务完成后，记录到记忆中
    memory_summary = f"已完成任务: {task}\n最终结果: 成功修改了 'greet' 函数。"
    record_memory(memory_summary)

    print("--- 任务流程结束 ---")