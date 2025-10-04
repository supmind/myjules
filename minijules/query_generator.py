import logging
from typing import List, Dict, Any

from autogen_core.models import SystemMessage, UserMessage
from minijules.agents import OpenAIChatCompletionClient

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """
您是一位资深的软件工程师，擅长通过分析任务需求和现有代码库的结构来快速定位关键代码。
您的任务是根据用户提供的“原始任务”和“项目结构概览”，生成一组简洁、精确的检索查询关键词。

这些关键词应该能帮助您找到：
1.  与任务最直接相关的代码块（例如，特定的函数或类）。
2.  过去完成的、可能与当前任务相似的任务历史记录。

请遵循以下规则：
-   仔细分析任务描述，理解其核心意图。
-   在项目结构中寻找与任务描述最匹配的文件、类或函数名。
-   基于您的分析，提供一个JSON格式的字符串，其中包含一个名为 "queries" 的列表。
-   列表中的每个字符串都应该是一个高度相关的、用于向量数据库检索的查询。
-   查询应该简洁明了，例如 "function for calculating user age", "class UserProfile", "how to handle database connections" 等。
-   生成的查询总数不应超过 5 个。

示例输入:
原始任务: "在用户个人资料页面添加一个显示用户年龄的功能。用户的出生日期存储在 UserProfile 类中。"
项目结构概览:
-   📁 models/user.py
    - class UserProfile
        - def __init__
        - def get_birthdate
-   📁 routes/profile.py
    - def render_profile_page

示例输出:
{
    "queries": [
        "UserProfile class in models/user.py",
        "function get_birthdate",
        "render_profile_page function",
        "calculate user age from birthdate"
    ]
}
"""

async def generate_smart_queries(
    task_string: str,
    project_structure: str,
    client: OpenAIChatCompletionClient,
) -> List[str]:
    """
    使用 LLM 分析任务和代码结构，生成智能检索查询。
    """
    logger.info("正在生成智能检索查询...")
    try:
        user_prompt = f"""
# 原始任务
{task_string}

# 当前项目结构概览
{project_structure}

请根据以上信息，生成您的JSON格式的检索查询。
"""
        response = await client.create(
            messages=[
                SystemMessage(content=SYSTEM_PROMPT),
                UserMessage(content=user_prompt),
            ]
        )

        response_text = response.content
        if not isinstance(response_text, str):
            response_text = str(response_text)

        # 提取并解析JSON
        import json
        json_part = response_text[response_text.find('{'):response_text.rfind('}')+1]
        queries_dict = json.loads(json_part)

        smart_queries = queries_dict.get("queries", [])
        if not isinstance(smart_queries, list):
            logger.warning(f"LLM返回的'queries'不是一个列表: {smart_queries}")
            return []

        logger.info(f"成功生成智能查询: {smart_queries}")
        return smart_queries

    except Exception as e:
        logger.error(f"生成智能查询时发生错误: {e}")
        # 在发生错误时回退到使用原始任务作为查询
        return [task_string]