import asyncio
import logging
from pathlib import Path
from typing import List, Dict, Any

from autogen_ext.memory.chromadb import ChromaDBVectorMemory, PersistentChromaDBVectorMemoryConfig, SentenceTransformerEmbeddingFunctionConfig
from autogen_core.memory import MemoryContent, MemoryMimeType
from tree_sitter_language_pack import get_language, get_parser

# --- 全局配置 ---
logger = logging.getLogger(__name__)

# 定义工作区和数据库路径
WORKSPACE_DIR = Path(__file__).parent.resolve() / "workspace"
DB_PATH = Path(__file__).parent.resolve() / "chroma_db"
CODE_COLLECTION_NAME = "code_index_v2"  # 使用新版本号以避免与旧数据冲突
MEMORY_COLLECTION_NAME = "memory_index_v2"

# --- Tree-sitter 多语言配置 (保持不变) ---
LANGUAGES = {
    ".py": "python",
    ".js": "javascript",
    ".go": "go",
    ".rs": "rust",
}
TOP_LEVEL_NODE_TYPES = {
    "python": ["function_definition", "class_definition"],
    "javascript": ["function_declaration", "class_declaration", "lexical_declaration"],
    "go": ["function_declaration", "type_declaration"],
    "rust": ["function_item", "struct_item"],
}
COMMENT_NODE_TYPES = {
    "python": ["comment"],
    "javascript": ["comment"],
    "go": ["comment"],
    "rust": ["line_comment", "block_comment"],
}

# --- 新的、基于 AutoGen Memory 的 RAG 和历史记忆实例 ---

# 用于代码检索的 RAG 内存
code_rag_memory = ChromaDBVectorMemory(
    config=PersistentChromaDBVectorMemoryConfig(
        collection_name=CODE_COLLECTION_NAME,
        persistence_path=str(DB_PATH),
        k=5,  # 检索前5个最相关的代码块
        score_threshold=0.4,
        embedding_function_config=SentenceTransformerEmbeddingFunctionConfig(
            model_name='BAAI/bge-large-en-v1.5'
        ),
    )
)

# 用于存储和检索已完成任务历史的内存
task_history_memory = ChromaDBVectorMemory(
    config=PersistentChromaDBVectorMemoryConfig(
        collection_name=MEMORY_COLLECTION_NAME,
        persistence_path=str(DB_PATH),
        k=1, # 只检索最相关的一个历史任务
        score_threshold=0.5,
        embedding_function_config=SentenceTransformerEmbeddingFunctionConfig(
            model_name='BAAI/bge-large-en-v1.5'
        ),
    )
)

# --- 代码分块逻辑 (大部分保持不变) ---

def _traverse_and_collect(node, language, code_blocks, comments):
    """递归地遍历 AST，收集顶层代码块和所有注释。"""
    if node.type in TOP_LEVEL_NODE_TYPES.get(language, []):
        if language == 'javascript' and node.type == 'lexical_declaration':
            var_declarator = node.child(0)
            if var_declarator and var_declarator.child_by_field_name('value') and var_declarator.child_by_field_name('value').type == 'arrow_function':
                 code_blocks.append(var_declarator)
        else:
            code_blocks.append(node)
    if node.type in COMMENT_NODE_TYPES.get(language, []):
        comments.append(node)
    for child in node.children:
        _traverse_and_collect(child, language, code_blocks, comments)

def extract_chunks(file_path: Path, language: str) -> List[Dict[str, Any]]:
    try:
        parser = get_parser(language)
        code = file_path.read_text(encoding='utf-8')
        tree = parser.parse(bytes(code, "utf8"))

        all_code_blocks, all_comments = [], []
        _traverse_and_collect(tree.root_node, language, all_code_blocks, all_comments)

        top_level_blocks = [node for node in all_code_blocks if node.parent == tree.root_node]
        comment_map = {c.end_point[0]: c.text.decode('utf8') for c in all_comments}
        code_lines = code.splitlines()

        chunks = []
        for node in top_level_blocks:
            name_node = node.child_by_field_name("name") or next((c for c in node.children if c.type in ['identifier', 'type_identifier']), None)
            block_name = name_node.text.decode('utf8') if name_node else "anonymous"
            block_code = node.text.decode('utf8')
            associated_comment = "无文档。"

            if language == "python":
                body_node = node.child_by_field_name("body")
                if body_node and body_node.type == "block" and body_node.named_child_count > 0:
                    first_child = body_node.named_child(0)
                    if first_child.type == "expression_statement" and first_child.named_child_count > 0:
                        string_node = first_child.named_child(0)
                        if string_node.type == "string":
                            docstring_content = string_node.text.decode('utf-8').strip('\'\"')
                            associated_comment = docstring_content.strip()

            if associated_comment == "无文档。":
                preceding_line_index = node.start_point[0] - 1
                if preceding_line_index >= 0 and code_lines[preceding_line_index].strip().startswith(('#', '//')):
                    associated_comment = comment_map.get(preceding_line_index, "无文档。")

            document = f"FILEPATH: {file_path.relative_to(WORKSPACE_DIR)}\nNAME: {block_name}\nDOCS: {associated_comment}\n\n{block_code}"
            metadata = {"filepath": str(file_path.relative_to(WORKSPACE_DIR)), "name": block_name, "comment": associated_comment}
            chunks.append({"content": document, "metadata": metadata})

        return chunks
    except Exception as e:
        logger.error(f"解析 {file_path} 失败: {e}")
        return []

async def index_workspace():
    """
    索引整个工作区，使用新的 ChromaDBVectorMemory。
    """
    logger.info("清空现有代码索引...")
    await code_rag_memory.clear()

    logger.info("开始索引工作区文件...")
    total_chunks = 0
    for fp in WORKSPACE_DIR.rglob('*'):
        if fp.is_file() and fp.suffix in LANGUAGES:
            chunks = extract_chunks(fp, LANGUAGES[fp.suffix])
            if chunks:
                memory_contents = [
                    MemoryContent(content=chunk['content'], mime_type=MemoryMimeType.TEXT, metadata=chunk['metadata'])
                    for chunk in chunks
                ]
                await code_rag_memory.add(memory_contents)
                total_chunks += len(chunks)

    logger.info(f"索引完成。共处理 {total_chunks} 个代码块。")

# 旧的函数 retrieve_context, save_memory, retrieve_memory 已被移除，
# 因为它们的功能现在由 code_rag_memory 和 task_history_memory 对象直接提供。

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

    async def main():
        # 创建一个测试文件
        (WORKSPACE_DIR / "math").mkdir(parents=True, exist_ok=True)
        (WORKSPACE_DIR / "math/operations.py").write_text("""
# 这是一个计算器类。
class Calculator:
    '''一个用于执行基本数学运算的类。'''
    def add(self, a, b):
        # 将两个数相加
        return a + b

def subtract(a, b):
    # 将两个数相减
    return a - b
""")
        # 索引工作区
        await index_workspace()

        # 检索
        query = "如何将两个数相加？"
        results = await code_rag_memory.query(query)

        logger.info(f"\n--- 对 '{query}' 的检索结果 ---")
        if results:
            for res in results:
                logger.info(f"相似度分数: {res.metadata.get('score')}")
                logger.info(res.content)
                logger.info("-" * 20)
        else:
            logger.info("未找到相关文档。")

    asyncio.run(main())