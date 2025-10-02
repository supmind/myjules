import os
import chromadb
from pathlib import Path
from sentence_transformers import SentenceTransformer
from tree_sitter_language_pack import get_language, get_parser
from tree_sitter import Parser
from typing import List, Dict, Any

# --- 全局配置 ---

# 定义工作区和数据库路径
WORKSPACE_DIR = Path(__file__).parent.resolve() / "workspace"
DB_PATH = Path(__file__).parent.resolve() / "chroma_db"
CODE_COLLECTION_NAME = "code_index"
MEMORY_COLLECTION_NAME = "memory_index"

# 加载句子转换器模型 (已升级)
model = SentenceTransformer('BAAI/bge-large-en-v1.5')

# --- Tree-sitter 多语言配置 ---

LANGUAGES = {
    ".py": "python",
    ".js": "javascript",
    ".go": "go",
    ".rs": "rust",
}

# 节点类型配置
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

def _traverse_and_collect(node, language, code_blocks, comments):
    """递归地遍历 AST，收集顶层代码块和所有注释。"""
    # 检查当前节点是否是顶层代码块
    if node.type in TOP_LEVEL_NODE_TYPES.get(language, []):
        if language == 'javascript' and node.type == 'lexical_declaration':
            var_declarator = node.child(0)
            if var_declarator and var_declarator.child_by_field_name('value') and var_declarator.child_by_field_name('value').type == 'arrow_function':
                 code_blocks.append(var_declarator)
        else:
            code_blocks.append(node)

    # 检查当前节点是否是注释
    if node.type in COMMENT_NODE_TYPES.get(language, []):
        comments.append(node)

    # 递归遍历所有子节点
    for child in node.children:
        _traverse_and_collect(child, language, code_blocks, comments)


def extract_chunks(file_path: Path, language: str) -> List[Dict[str, Any]]:
    try:
        parser = get_parser(language)
        code = file_path.read_text(encoding='utf-8')
        tree = parser.parse(bytes(code, "utf8"))

        all_code_blocks, all_comments = [], []
        _traverse_and_collect(tree.root_node, language, all_code_blocks, all_comments)

        # 只保留顶层代码块
        top_level_blocks = [node for node in all_code_blocks if node.parent == tree.root_node]

        comment_map = {c.end_point[0]: c.text.decode('utf8') for c in all_comments}
        code_lines = code.splitlines()

        chunks = []
        for node in top_level_blocks:
            name_node = node.child_by_field_name("name") or next((c for c in node.children if c.type in ['identifier', 'type_identifier']), None)
            block_name = name_node.text.decode('utf8') if name_node else "anonymous"
            block_code = node.text.decode('utf8')

            preceding_line_index = node.start_point[0] - 1
            associated_comment = "无文档。"

            if preceding_line_index >= 0 and code_lines[preceding_line_index].strip() != "":
                associated_comment = comment_map.get(preceding_line_index, "无文档。")

            document_lines = [f"// FILEPATH: {file_path.relative_to(WORKSPACE_DIR)}", f"// NAME: {block_name}", f"// DOCS: {associated_comment.strip()}", f"\n{block_code}"]
            metadata = {"filepath": str(file_path.relative_to(WORKSPACE_DIR)), "name": block_name, "comment": associated_comment.strip()}
            document = "\n".join(document_lines)
            chunk_id = f"{file_path.relative_to(WORKSPACE_DIR)}::{block_name}"
            chunks.append({"document": document, "metadata": metadata, "id": chunk_id})

        return chunks
    except Exception as e:
        print(f"解析 {file_path} 失败: {e}")
        return []

def index_workspace():
    client = chromadb.PersistentClient(path=str(DB_PATH))
    try: client.delete_collection(name=CODE_COLLECTION_NAME)
    except Exception: pass
    collection = client.get_or_create_collection(name=CODE_COLLECTION_NAME)

    all_chunks = [chunk for fp in WORKSPACE_DIR.rglob('*') if fp.is_file() and fp.suffix in LANGUAGES for chunk in extract_chunks(fp, LANGUAGES[fp.suffix])]

    if not all_chunks: return

    collection.add(
        documents=[c['document'] for c in all_chunks],
        metadatas=[c['metadata'] for c in all_chunks],
        ids=[c['id'] for c in all_chunks],
        embeddings=model.encode([c['document'] for c in all_chunks]).tolist()
    )

def retrieve_context(query: str, n_results: int = 3) -> List[str]:
    client = chromadb.PersistentClient(path=str(DB_PATH))
    try:
        collection = client.get_collection(name=CODE_COLLECTION_NAME)
        results = collection.query(query_embeddings=model.encode([query]).tolist(), n_results=n_results)
        return results['documents'][0] if results and results.get('documents') else []
    except ValueError:
        return []

def save_memory(task_summary: str, final_code_diff: str):
    """Saves the summary of a completed task to the memory collection."""
    try:
        client = chromadb.PersistentClient(path=str(DB_PATH))
        collection = client.get_or_create_collection(name=MEMORY_COLLECTION_NAME)

        document = f"任务总结:\n{task_summary}\n\n最终代码变更:\n{final_code_diff}"
        memory_id = f"memory_{collection.count() + 1}"

        collection.add(
            documents=[document],
            ids=[memory_id],
            embeddings=model.encode([document]).tolist()
        )
        print(f"--- ✅ 成功将任务经验存入记忆库 (ID: {memory_id}) ---")
    except Exception as e:
        print(f"--- ⚠️ 存入记忆时发生错误: {e} ---")

def retrieve_memory(query: str, n_results: int = 1) -> List[str]:
    """Retrieves similar past experiences from the memory collection."""
    try:
        client = chromadb.PersistentClient(path=str(DB_PATH))
        collection = client.get_collection(name=MEMORY_COLLECTION_NAME)
        if collection.count() == 0:
            return []

        results = collection.query(
            query_embeddings=model.encode([query]).tolist(),
            n_results=n_results
        )
        return results['documents'][0] if results and results.get('documents') else []
    except ValueError:
        return [] # Collection might be empty or not exist
    except Exception as e:
        print(f"--- ⚠️ 检索记忆时发生错误: {e} ---")
        return []

if __name__ == '__main__':
    (WORKSPACE_DIR / "math").mkdir(parents=True, exist_ok=True)
    (WORKSPACE_DIR / "math/operations.py").write_text("""
# 这是一个计算器类。
class Calculator:
    def add(self, a, b):
        return a + b
# 这是一个独立的减法函数。
def subtract(a, b):
    return a - b
    """)
    index_workspace()
    retrieved_docs = retrieve_context("subtract function", n_results=1)
    if retrieved_docs: print(retrieved_docs[0])