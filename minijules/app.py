import asyncio
import json
import os
import logging
from pathlib import Path
from dotenv import load_dotenv
import argparse
from dataclasses import dataclass, field
from typing import Dict, List, Any

# 导入新的 autogen 模块
from autogen_agentchat.agents import CodeExecutorAgent
from autogen_agentchat.teams import RoundRobinGroupChat
from autogen_ext.code_executors.local import LocalCommandLineCodeExecutor
from autogen_agentchat.conditions import MaxMessageTermination, TextMentionTermination
from autogen_core.memory import MemoryContent, MemoryMimeType
from autogen_ext.models.openai import OpenAIChatCompletionClient
from autogen_core.models import SystemMessage, UserMessage

# 导入重构后的项目模块
import minijules.tools as tools
import minijules.indexing as indexing
from minijules.agents import create_core_agent

# --- 日志配置 ---
def setup_logging():
    """配置全局日志记录器。"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

# --- 应用配置常量 ---
MAX_STEPS = 30

# --- 结构化状态管理器 ---
@dataclass
class TaskState:
    """封装与单个任务相关的所有状态。"""
    task_string: str
    work_history: List[str] = field(default_factory=list)

# --- 配置加载 ---
def load_llm_config_list():
    """从 .env 文件或环境变量加载 LLM 配置列表。"""
    load_dotenv()
    config_list_json = os.environ.get("OAI_CONFIG_LIST")
    if config_list_json:
        try:
            return json.loads(config_list_json)
        except Exception as e:
            logger.error(f"无法解析 OAI_CONFIG_LIST 环境变量。错误: {e}")
            return None
    logger.error("LLM 配置未找到。请参考 .env.template 设置您的配置。")
    return None

# --- 新的主应用 ---
class JulesApp:
    """
    MiniJules 主应用程序类 (v0.4 重构版)。
    """
    def __init__(self, task_string: str, config_list: List[Dict], max_steps: int = MAX_STEPS):
        self.state = TaskState(task_string=task_string)
        self.max_steps = max_steps
        self.config_list = config_list

        # 1. 创建核心代理
        self.core_agent = create_core_agent(config_list)

        # 2. 初始化代码执行代理
        # 在这里动态创建执行器，以便它能获取被测试 monkeypatch 过的 WORKSPACE_DIR
        code_executor = LocalCommandLineCodeExecutor(work_dir=str(tools.WORKSPACE_DIR))
        self.code_executor_agent = CodeExecutorAgent(
            name="CodeExecutor",
            code_executor=code_executor,
        )

        # 3. 为核心代理注册工具
        self.core_agent.tools = [
            tools.list_project_structure,
            tools.list_files,
            tools.read_file,
            tools.create_file_with_block,
            tools.overwrite_file_with_block,
            tools.replace_with_git_merge_diff,
            tools.delete_file,
            tools.run_in_bash_session,
            tools.apply_patch,
            tools.git_status,
            tools.git_diff,
            tools.git_add,
            tools.git_commit,
            tools.git_create_branch,
            self._request_user_input,
            self._request_code_review,
            self._task_complete,
        ]

        # 4. 为核心代理配置记忆系统
        self.core_agent.memory = [indexing.code_rag_memory, indexing.task_history_memory]

        # 5. 定义群聊终止条件
        termination_condition = (
            TextMentionTermination("TERMINATE") | MaxMessageTermination(self.max_steps)
        )

        # 6. **关键修复**: 创建群聊，使用 'participants' 关键字参数
        self.group_chat = RoundRobinGroupChat(
            participants=[self.core_agent, self.code_executor_agent],
            termination_condition=termination_condition,
        )

    def _request_user_input(self, message: str) -> str:
        """[工具] 向用户请求输入。"""
        logger.info(f"向用户请求输入: {message}")
        user_response = input(f"❓ {message}\n> ")
        return f"用户提供了以下指导: {user_response}"

    async def _request_code_review(self) -> str:
        """[工具] 请求对当前代码变更进行评审。"""
        logger.info("请求代码评审...")
        try:
            # 1. 创建一个临时的LLM客户端用于评审
            # 确保config_list不为空
            if not self.config_list:
                return "错误: LLM配置不可用, 无法执行代码评审。"

            # 使用列表中的第一个配置
            config = self.config_list[0]
            reviewer_client = OpenAIChatCompletionClient(
                model=config.get("model"),
                api_key=config.get("api_key"),
            )

            # 2. 准备评审所需的内容
            code_diff = tools.git_diff()
            if "无变更" in code_diff:
                return "代码无变更，无需评审。"

            task_description = self.state.task_string

            # 3. 构建评审提示
            reviewer_system_prompt = """您是一位资深的软件架构师和代码评审专家。您的任务是严格审查所提供的代码变更。
请根据以下标准进行评估：
1.  **目标符合度**: 代码变更是否完全、准确地实现了原始任务的要求？
2.  **正确性与Bug**: 代码逻辑是否正确？是否存在潜在的运行时错误、逻辑漏洞或边缘情况处理不当的问题？
3.  **代码质量**: 代码是否清晰、可读、可维护？是否遵循了通用的最佳实践？
4.  **完整性**: 变更是否完整？例如，如果添加了新功能，是否也添加了相应的单元测试？

您的输出应该是一个简洁的Markdown格式的评审报告。如果代码质量很高，请以 `#Correct#` 开头。如果有问题，请清晰地列出需要修改的地方。"""

            review_prompt = f"""
### 原始任务
{task_description}

### 代码变更 (Git Diff)
```diff
{code_diff}
```

请根据上述标准提供您的评审报告。"""

            # 4. 调用LLM进行评审
            response = await reviewer_client.create(
                messages=[
                    SystemMessage(content=reviewer_system_prompt),
                    UserMessage(content=review_prompt, source="code-reviewer-prompt")
                ]
            )

            review_content = response.content
            if not isinstance(review_content, str):
                 review_content = str(review_content)


            logger.info(f"代码评审完成:\n{review_content}")
            return f"代码评审结果:\n{review_content}"

        except Exception as e:
            error_message = f"执行代码评审时发生意外错误: {e}"
            logger.error(error_message)
            return error_message

    async def _task_complete(self, summary: str) -> str:
        """[工具] 处理任务完成信号。"""
        logger.info("任务完成工具被调用，正在保存任务经验...")
        try:
            final_diff = tools.git_diff()
            full_summary_doc = f"原始任务: {self.state.task_string}\n工作总结: {summary}\n\n最终代码变更:\n{final_diff}"

            await indexing.task_history_memory.add(
                MemoryContent(content=full_summary_doc, mime_type=MemoryMimeType.TEXT)
            )

            success_message = f"任务已成功完成，并已存入记忆库: {summary}"
            logger.info(success_message)
            return success_message
        except Exception as e:
            error_message = f"存入记忆时发生错误: {e}"
            logger.error(error_message)
            return error_message

    async def run(self):
        """运行主应用流程。"""
        logger.info(f"接收到任务: {self.state.task_string}")

        logger.info("正在异步索引工作区...")
        await indexing.index_workspace()
        logger.info("工作区索引完成。")

        logger.info("--- 任务流程开始 ---")
        chat_result = await self.group_chat.run(task=self.state.task_string)
        logger.info("--- 任务流程结束 ---")

        if chat_result.stop_reason:
            logger.info(f"任务终止原因: {chat_result.stop_reason}")

        self.state.work_history = [msg.to_text() for msg in chat_result.messages]
        logger.info("\n--- 对话历史回顾 ---")
        for msg_text in self.state.work_history:
            logger.info(msg_text)
            logger.info("-" * 20)

async def main():
    """程序主入口。"""
    setup_logging()

    config_list = load_llm_config_list()
    if not config_list: return

    parser = argparse.ArgumentParser(
        description="MiniJules: 一个基于 AutoGen v0.4 的AI开发助手。",
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("task", type=str, help="要执行的主要任务描述。")
    parser.add_argument("--max-steps", type=int, default=MAX_STEPS, help=f"设置最大对话轮次 (默认: {MAX_STEPS})。")
    args = parser.parse_args()

    app = JulesApp(
        task_string=args.task,
        config_list=config_list,
        max_steps=args.max_steps
    )
    await app.run()

if __name__ == "__main__":
    asyncio.run(main())