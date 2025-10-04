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
from minijules import query_generator
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
    plan: str = ""
    current_step_index: int = 0
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
        code_executor = LocalCommandLineCodeExecutor(work_dir=str(tools.WORKSPACE_DIR))
        self.code_executor_agent = CodeExecutorAgent(
            name="CodeExecutor",
            code_executor=code_executor,
        )

        # 3. 为核心代理注册工具
        self.core_agent.tools = [
            # 计划和状态管理工具
            self.set_plan,
            self.plan_step_complete,
            # 文件系统和代码分析工具
            tools.list_project_structure,
            tools.list_files,
            tools.read_file,
            tools.create_file_with_block,
            tools.overwrite_file_with_block,
            tools.replace_with_git_merge_diff,
            tools.delete_file,
            tools.apply_patch,
            # 执行和版本控制工具
            tools.run_in_bash_session,
            self.run_tests_and_debug,
            tools.git_status,
            tools.git_diff,
            tools.git_add,
            tools.git_commit,
            tools.git_create_branch,
            # 用户交互和任务完成工具
            self.message_user,
            self.request_user_input,
            self.request_code_review,
            self.pre_commit_instructions,
            self.submit,
            self.task_complete,
        ]

        # 4. 为核心代理配置记忆系统
        self.core_agent.memory = [indexing.code_rag_memory, indexing.task_history_memory]

        # 5. 定义群聊终止条件
        termination_condition = (
            TextMentionTermination("TERMINATE") | MaxMessageTermination(self.max_steps)
        )

        # 6. 创建群聊
        self.group_chat = RoundRobinGroupChat(
            participants=[self.core_agent, self.code_executor_agent],
            termination_condition=termination_condition,
        )

    def set_plan(self, plan: str) -> str:
        """[工具] 设置或更新任务计划。"""
        self.state.plan = plan
        self.state.current_step_index = 1
        logger.info(f"计划已更新:\n{plan}")
        return f"计划已成功设置。当前步骤 1/{len(plan.splitlines())}。"

    def plan_step_complete(self, message: str) -> str:
        """[工具] 标记当前计划步骤已完成。"""
        total_steps = len(self.state.plan.splitlines())
        if self.state.current_step_index >= total_steps:
            return "所有计划步骤均已完成。"

        logger.info(f"步骤 {self.state.current_step_index}/{total_steps} 已完成: {message}")
        self.state.current_step_index += 1
        return f"步骤已完成。下一步: {self.state.current_step_index}/{total_steps}。"

    async def message_user(self, message: str, continue_working: bool = False) -> str:
        """[工具] 向用户发送消息。"""
        logger.info(f"给用户的消息: {message}")
        if not continue_working:
            return "任务已由 agent 暂停，等待用户反馈。请在准备好后重新运行。TERMINATE"
        return "消息已发送。"

    async def pre_commit_instructions(self) -> str:
        """[工具] 返回预提交指令。"""
        instructions = """\
# 预提交检查清单

在您提交代码之前，请仔细检查并完成以下步骤：

1.  **运行所有测试**: 确保您的更改没有破坏任何现有功能。
    - 使用 `run_in_bash_session` 运行 `python3 -m pytest`。

2.  **代码审查**: 请求一次最终的代码审查。
    - 使用 `request_code_review` 工具。分析评审结果，如果需要，进行修改。

3.  **最终验证**: 在提交之前，最后一次审视您的代码变更。
    - 使用 `git_diff` 检查最终的变更。
    - 确保没有遗留任何调试代码或不必要的注释。

4.  **反思和总结**: 准备好一个清晰的提交信息。
    - 总结您所做的工作、解决的问题以及实现方式。

完成以上所有步骤后，您就可以使用 `submit` 工具来提交您的工作了。
"""
        return instructions

    async def submit(self, branch_name: str, commit_message: str, title: str, description: str) -> str:
        """[工具] 提交工作并终止任务。"""
        logger.info("--- 任务提交 ---")
        logger.info(f"分支: {branch_name}")
        logger.info(f"标题: {title}")
        logger.info(f"描述: {description}")
        logger.info(f"提交信息:\n{commit_message}")
        logger.info("-----------------")

        summary = f"任务以标题 '{title}' 成功提交在分支 '{branch_name}'。"
        await self.task_complete(summary)

        return "任务已成功提交。工作流程终止。TERMINATE"

    async def request_user_input(self, message: str) -> str:
        """[工具] 向用户请求输入，并暂停工作流。"""
        logger.info(f"向用户请求输入: {message}")
        return f"Agent 请求用户输入: '{message}'. 工作流已暂停。TERMINATE"

    async def run_tests_and_debug(self, test_command: str = "python3 -m pytest", max_retries: int = 3) -> str:
        """
        [高级工具] 运行测试命令，如果失败，则尝试自动调试和修复。
        """
        logger.info(f"开始使用命令 '{test_command}' 进行测试和调试...")
        try:
            if not self.config_list:
                return "错误: LLM配置不可用, 无法执行调试。"

            # 创建一个临时的LLM客户端用于修复
            config = self.config_list[0]
            client = OpenAIChatCompletionClient(
                model=config.get("model"),
                api_key=config.get("api_key"),
                base_url=config.get("base_url"),
            )

            return await tools.run_tests_and_debug(
                test_command=test_command,
                client=client,
                max_retries=max_retries
            )
        except Exception as e:
            error_message = f"执行测试和调试时发生意外错误: {e}"
            logger.error(error_message)
            return error_message

    async def request_code_review(self) -> str:
        """[工具] 请求对当前代码变更进行评审。"""
        logger.info("请求代码评审...")
        try:
            if not self.config_list:
                return "错误: LLM配置不可用, 无法执行代码评审。"

            config = self.config_list[0]
            reviewer_client = OpenAIChatCompletionClient(
                model=config.get("model"),
                api_key=config.get("api_key"),
                base_url=config.get("base_url"),
            )

            code_diff = tools.git_diff()
            if "无变更" in code_diff:
                return "代码无变更，无需评审。"

            task_description = self.state.task_string

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

    async def task_complete(self, summary: str) -> str:
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

        # 1. 索引工作区
        logger.info("正在异步索引工作区...")
        await indexing.index_workspace()
        logger.info("工作区索引完成。")

        # 2. 检索并构建增强的上下文
        enhanced_task_string = await self._retrieve_enhanced_context()
        logger.info("上下文增强完成，准备开始任务流程。")

        # 3. 开始任务流程
        logger.info("--- 任务流程开始 ---")
        chat_result = await self.group_chat.run(task=enhanced_task_string)
        logger.info("--- 任务流程结束 ---")

        if chat_result.stop_reason:
            logger.info(f"任务终止原因: {chat_result.stop_reason}")

        self.state.work_history = [msg.to_text() for msg in chat_result.messages]
        logger.info("\n--- 对话历史回顾 ---")
        for msg_text in self.state.work_history:
            logger.info(msg_text)
            logger.info("-" * 20)

    async def _retrieve_enhanced_context(self) -> str:
        """
        执行高级RAG流程：分析结构、生成查询、检索上下文，并构建最终的增强任务字符串。
        """
        # 分析项目结构
        logger.info("正在分析项目结构...")
        project_structure = tools.list_project_structure()

        # 生成智能查询
        query_client = OpenAIChatCompletionClient(
            model=self.config_list[0].get("model"),
            api_key=self.config_list[0].get("api_key"),
            base_url=self.config_list[0].get("base_url"),
        )
        smart_queries = await query_generator.generate_smart_queries(
            task_string=self.state.task_string,
            project_structure=project_structure,
            client=query_client
        )

        # 执行检索并收集上下文
        logger.info(f"使用智能查询进行检索: {smart_queries}")
        retrieved_context = []
        retrieved_content_set = set()

        all_queries = [self.state.task_string] + smart_queries
        for query in all_queries:
            code_results = await indexing.code_rag_memory.query(query)
            history_results = await indexing.task_history_memory.query(query)

            for res in code_results + history_results:
                if res.content not in retrieved_content_set:
                    retrieved_context.append(res.content)
                    retrieved_content_set.add(res.content)

        context_str = "\n\n---\n\n".join(retrieved_context)
        logger.info(f"共检索到 {len(retrieved_context)} 条相关上下文。")

        # 构建最终的增强任务描述
        enhanced_task_string = f"""
# 原始任务
{self.state.task_string}

# 当前项目结构概览
{project_structure}

# 基于智能查询检索到的相关上下文
以下是根据任务分析检索到的、可能最相关的代码块和历史任务记录。请优先参考这些信息。
---
{context_str if context_str else "未检索到额外上下文。"}
---

请根据以上所有信息，开始您的工作。
"""
        return enhanced_task_string

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