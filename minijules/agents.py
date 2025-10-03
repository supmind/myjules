import autogen
import json

# --- 代理配置 ---
# 共享的 LLM 配置，确保所有代理使用相同的模型和设置
llm_config = {"cache_seed": 42, "temperature": 0}

from pathlib import Path

# --- 提示加载 ---
def load_system_prompt(filename: str) -> str:
    """从文件加载系统提示。"""
    try:
        prompt_path = Path(__file__).parent / "prompts" / filename
        return prompt_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        print(f"错误: 未找到提示文件: {filename}")
        return "You are a helpful AI assistant." # Fallback prompt

# --- 新的核心代理 ---
core_agent = autogen.ConversableAgent(
    name="CoreAgent",
    system_message=load_system_prompt("core_agent_system_prompt.txt"),
    llm_config=llm_config,
)


# --- 用户代理 ---
# UserProxy 仍然作为与 CoreAgent 对话的主要入口和流程控制器
user_proxy = autogen.UserProxyAgent(
    name="UserProxy",
    human_input_mode="NEVER",  # 在新架构中，我们将以编程方式提供输入
    max_consecutive_auto_reply=100, # 允许更长的对话链
    # is_termination_msg 和 code_execution_config 在新架构中不再由 UserProxy 直接使用
    # 但保留它们以备将来的扩展
    is_termination_msg=lambda x: x.get("content", "").strip().endswith("TERMINATE"),
    code_execution_config={"work_dir": "workspace", "use_docker": False},
)

def assign_llm_config(config_list: list):
    """将从主应用加载的 config_list 分配给 CoreAgent。"""
    core_agent.llm_config["config_list"] = config_list