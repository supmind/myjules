import asyncio
import json
import os
import logging
from pathlib import Path
from dotenv import load_dotenv
import argparse
from typing import Dict, List, Any
import git
from git.exc import InvalidGitRepositoryError
import base64
import io
import requests
from PIL import Image

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
from minijules.types import TaskState

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
    这个类现在主要负责应用的生命周期、状态管理和流程协调。
    所有工具的实现都已迁移到 `tools.py` 模块。
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
        # 有状态的工具作为实例方法直接注册，无状态的工具从 tools 模块导入。
        self.core_agent.tools = [
            # 计划和状态管理工具 (在 App 中实现)
            self.set_plan,
            self.record_user_approval_for_plan,
            self.plan_step_complete,
            # 信息检索与外部知识 (部分在 App 中实现)
            tools.google_search,
            tools.view_text_website,
            self.view_image,
            self.read_image_file,
            # 文件系统和代码分析工具 (无状态，在 tools.py 中)
            tools.read_agents_md,
            tools.list_project_structure,
            tools.grep,
            tools.list_files,
            tools.read_file,
            tools.create_file_with_block,
            tools.overwrite_file_with_block,
            tools.replace_with_git_merge_diff,
            tools.delete_file,
            tools.rename_file,
            tools.apply_patch,
            # 执行和版本控制工具 (部分在 App 中实现)
            tools.run_in_bash_session,
            self.run_tests_and_debug_app,
            tools.git_status,
            tools.git_diff,
            tools.git_add,
            tools.git_commit,
            tools.git_create_branch,
            tools.restore_file,
            tools.reset_all,
            # 用户交互和任务完成工具 (在 App 中实现)
            self.message_user,
            self.request_user_input,
            self.request_code_review,
            self.initiate_memory_recording,
            self.pre_commit_instructions,
            self.submit,
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

    # --- 有状态工具的实现（移回 app.py 以解决工具签名问题） ---

    def set_plan(self, plan: str) -> str:
        """[工具] 设置或更新任务计划。"""
        self.state.plan = plan
        self.state.current_step_index = 1
        logger.info(f"计划已更新:\n{plan}")
        num_steps = len([line for line in plan.splitlines() if line.strip()])
        return f'计划已成功设置。当前步骤 1/{num_steps}。'

    def record_user_approval_for_plan(self) -> str:
        """[工具] 记录用户对当前计划的批准。"""
        if not self.state.plan:
            return '错误: 尚未制定任何计划，无法批准。'
        self.state.plan_approved = True
        logger.info("用户已批准计划。现在可以开始执行。")
        return '计划已获批准。您现在可以开始执行第一个步骤。'

    def plan_step_complete(self, message: str) -> str:
        """[工具] 标记当前计划步骤已完成。"""
        if not self.state.plan_approved:
            return '错误: 计划尚未获得用户批准。请先使用 `request_user_input` 请求批准，并在用户同意后调用 `record_user_approval_for_plan`。'
        total_steps = len([line for line in self.state.plan.splitlines() if line.strip()])
        if self.state.current_step_index >= total_steps:
            return '所有计划步骤均已完成。'
        logger.info(f"步骤 {self.state.current_step_index}/{total_steps} 已完成: {message}")
        self.state.current_step_index += 1
        return f'步骤已完成。下一步: {self.state.current_step_index}/{total_steps}。'

    async def message_user(self, message: str, continue_working: bool = False) -> str:
        """[工具] 向用户发送消息。"""
        logger.info(f"给用户的消息: {message}")
        if not continue_working:
            return '任务已由 agent 暂停，等待用户反馈。请在准备好后重新运行。TERMINATE'
        return '消息已发送。'

    async def request_user_input(self, message: str) -> str:
        """[工具] 向用户请求输入，并暂停工作流。"""
        logger.info(f"向用户请求输入: {message}")
        return f"Agent 请求用户输入: '{message}'. 工作流已暂停。TERMINATE"

    async def submit(self, branch_name: str, commit_message: str, title: str, description: str) -> str:
        """[工具] 提交工作并终止任务。"""
        logger.info("--- 任务提交 ---")
        logger.info(f"分支: {branch_name}")
        logger.info(f"标题: {title}")
        logger.info(f"描述: {description}")
        logger.info(f"提交信息:\n{commit_message}")
        logger.info("-----------------")
        logger.info("任务完成，正在保存任务经验...")
        try:
            final_diff = tools.git_diff()
            full_summary_doc = f"原始任务: {self.state.task_string}\n工作总结: {description}\n\n最终代码变更:\n{final_diff}"
            await indexing.task_history_memory.add(
                MemoryContent(content=full_summary_doc, mime_type=MemoryMimeType.TEXT)
            )
            logger.info("任务经验已成功存入记忆库。")
        except Exception as e:
            logger.error(f"存入记忆时发生错误: {e}")
        summary = f"任务以标题 '{title}' 成功提交在分支 '{branch_name}'。"
        logger.info(summary)
        return "任务已成功提交。工作流程终止。TERMINATE"

    async def pre_commit_instructions(self) -> str:
        """[工具] 返回预提交指令。"""
        return await tools.pre_commit_instructions(self) # 这个可以保持不变，因为它没有参数

    async def initiate_memory_recording(self, learnings: str) -> str:
        """[工具] 记录在任务期间获得的关键学习。"""
        logger.info("正在记录学习经验...")
        try:
            learning_doc = f"通用学习经验:\n{learnings}"
            await indexing.task_history_memory.add(
                MemoryContent(content=learning_doc, mime_type=MemoryMimeType.TEXT)
            )
            success_message = '学习经验已成功存入记忆库。'
            logger.info(success_message)
            return success_message
        except Exception as e:
            error_message = f"存入记忆时发生错误: {e}"
            logger.error(error_message)
            return error_message

    async def request_code_review(self) -> str:
        """[工具] 请求对当前代码变更进行评审。"""
        return await tools.request_code_review(self)

    async def run_tests_and_debug_app(self, max_retries: int = 3) -> str:
        """[高级工具] 自动运行测试并进行调试。"""
        return await tools.run_tests_and_debug_app(self, max_retries)

    async def view_image(self, url: str) -> str:
        """[工具] 下载并描述来自 URL 的图像。"""
        return await tools.view_image(self, url)

    async def read_image_file(self, filepath: str) -> str:
        """[工具] 读取并描述本地文件系统中的图像文件。"""
        return await tools.read_image_file(self, filepath)


    async def _initialize_workspace_git(self):
        """
        确保工作区是一个 Git 仓库，并创建一个初始状态标签。
        这为 'restore_file' 和 'reset_all' 工具提供了基础。
        """
        logger.info("正在初始化或验证工作区的 Git 状态...")
        try:
            repo = git.Repo(tools.WORKSPACE_DIR)
            logger.info("Git 仓库已存在。")
        except InvalidGitRepositoryError:
            logger.info("未找到 Git 仓库，正在初始化一个新的...")
            repo = git.Repo.init(tools.WORKSPACE_DIR)
        except Exception as e:
            logger.error(f"访问 Git 仓库时发生未知错误: {e}")
            return

        # 确保有 user.name 和 user.email 配置，以避免提交错误
        try:
            repo.config_reader().get_value("user", "name")
            repo.config_reader().get_value("user", "email")
        except Exception:
            logger.info("正在设置默认的 Git 用户配置...")
            with repo.config_writer() as cw:
                cw.set_value("user", "name", tools.GIT_AUTHOR_NAME)
                cw.set_value("user", "email", tools.GIT_AUTHOR_EMAIL)

        # 检查仓库是否为空。如果是，则创建一个初始的空提交以确保 HEAD 有效。
        try:
            repo.head.commit
        except ValueError:
            logger.info("仓库为空，正在创建初始空提交以设置 HEAD...")
            repo.git.commit("--allow-empty", "-m", "chore: Initial empty commit for minijules setup")

        # 如果有其他未提交的变更或未跟踪的文件，创建另一个提交
        if repo.is_dirty(untracked_files=True):
            logger.info("检测到未提交的变更或未跟踪的文件，正在创建初始状态提交...")
            repo.git.add(A=True)
            repo.git.commit(m="chore: 保存任务开始前的初始工作区状态")

        # 删除旧标签（如果存在），然后创建新标签
        tag_name = "minijules-initial-state"
        if tag_name in repo.tags:
            logger.info(f"正在删除已存在的标签 '{tag_name}'...")
            repo.delete_tag(tag_name)

        logger.info(f"正在创建初始状态标签 '{tag_name}'...")
        repo.create_tag(tag_name, message="任务开始时的快照")
        logger.info("工作区 Git 状态初始化完成。")

    async def run(self):
        """运行主应用流程。"""
        logger.info(f"接收到任务: {self.state.task_string}")

        # 0. 初始化工作区 Git 状态
        await self._initialize_workspace_git()

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
        # **步骤 0: 读取 AGENTS.md**
        logger.info("正在检查 AGENTS.md...")
        agents_md_content = tools.read_agents_md()
        # 如果文件不存在，工具会返回一个信息字符串，我们在这里进行判断
        has_agents_md = "文件未找到" not in agents_md_content

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
        agents_md_section = ""
        if has_agents_md:
            agents_md_section = f"""
# **重要**: 项目特定指南 (AGENTS.md)
在开始任何工作之前，请仔细阅读并严格遵守以下项目特定指南：
---
{agents_md_content}
---
"""

        enhanced_task_string = f"""{agents_md_section}
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