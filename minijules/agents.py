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
    system_message="""您是一个专业的项目规划师。您的任务是接收一个高级目标和相关代码上下文，并将其分解成一个结构化的、可执行的工具调用计划。

您的输出**必须**是一个格式正确的 JSON 数组，其中每个对象都代表一个工具调用。
每个对象都必须包含两个键：`tool_name` (字符串) 和 `parameters` (一个包含该工具所有必需参数的字典)。

例如:
[
    {
        "tool_name": "create_file",
        "parameters": {
            "filename": "src/main.py",
            "content": "print('Hello, World!')"
        }
    },
    {
        "tool_name": "run_in_bash",
        "parameters": {
            "command": "python3 src/main.py"
        }
    }
]

在您的回复中，只包含这个 JSON 数组，不要有任何额外的解释、代码块标记（如 ```json）或客套话。""",
    llm_config=llm_config,
)

# 2. Executor Agent (执行者)
# 这个代理是实际的工作者。它接收单个具体的任务，并使用其工具来完成任务。
executor = autogen.AssistantAgent(
    name="Executor",
    system_message="""您是任务执行者。您将接收一个结构化的 JSON 对象，该对象代表一个需要执行的工具调用。
您的任务是解析这个对象，并使用您可用的工具来完成它。

例如，如果您收到以下指令：
{"tool_name": "read_file", "parameters": {"filename": "README.md"}}

您应该执行 `read_file` 工具，并将 `filename` 设置为 "README.md"。

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

def assign_llm_config(config_list: list):
    """将从主应用加载的 config_list 分配给所有代理。"""
    for agent in [planner, executor]:
        agent.llm_config["config_list"] = config_list