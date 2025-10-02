import autogen

# --- 代理配置 ---

# LLM 配置字典，用于初始化代理
# 注意：config_list 将从主应用 app.py 中传入
llm_config = {
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

重要提示：执行者可以通过 `write_to_scratchpad` 和 `read_scratchpad` 工具使用一个“便签”来在步骤之间传递信息。如果任务复杂，您可以在计划中明确指示执行者使用便签。
例如:
1. 读取文件 `config.json`，并将其中的 `api_key` 值写入便签。
2. 读取便签中的 `api_key`，并用它来创建一个新的 API 客户端。""",
    llm_config=llm_config,
)

# 2. Executor Agent (执行者)
# 这个代理是实际的工作者。它接收单个具体的任务，并使用其工具来完成任务。
executor = autogen.AssistantAgent(
    name="Executor",
    system_message="""您是任务执行者。您将接收一个具体的任务，并使用您可用的工具来完成它。
在完成每个任务后，报告您的结果。如果出现问题，请报告错误。

**代码修改指南**:
- **精确替换**: 当需要修改或重写一个现有函数时，**必须**使用 `replace_function_definition` 工具。它通过函数名进行定位，比简单的文本搜索更可靠。
- **精确插入**: 当需要向一个现有类添加新方法或属性时，**必须**使用 `insert_into_class_body` 工具。它能精确地将代码插入到类的末尾。
- **避免使用通用文本替换来修改代码**，因为那很脆弱且容易出错。

**您的工作流程应该是：**
1.  **思考**: 首先，考虑是否需要从便签中读取信息。使用 `read_scratchpad` 工具来获取之前步骤留下的上下文。
2.  **执行**: 使用您的其他工具来完成当前任务。优先使用 `replace_function_definition` 和 `insert_into_class_body` 来进行代码修改。
3.  **记录**: 如果您发现了任何对后续步骤有用的信息（例如，文件名、代码片段、配置值等），请使用 `write_to_scratchpad` 工具将其记录下来。

始终优先使用便签来维持任务的连续性。""",
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

def assign_llm_config(config_list: list):
    """将从主应用加载的 config_list 分配给所有代理。"""
    for agent in [planner, executor]:
        agent.llm_config["config_list"] = config_list