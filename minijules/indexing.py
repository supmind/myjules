import os
import chromadb
from pathlib import Path
from sentence_transformers import SentenceTransformer
from tree_sitter_languages import get_parser
from typing import List, Dict, Any

# --- 全局配置 ---

# 定义工作区和数据库路径
WORKSPACE_DIR = Path(__file__).parent.resolve() / "workspace"
DB_PATH = Path(__file__).parent.resolve() / "chroma_db"
COLLECTION_NAME = "code_index"

# 加载句子转换器模型
model = SentenceTransformer('all-MiniLM-L6-v2')

# --- Tree-sitter 多语言配置 ---

LANGUAGES = {
    ".py": "python",
    ".js": "javascript",
    ".go": "go",
}

QUERIES = {
    "python": """
    (function_definition name: (identifier) @name) @capture
    (class_definition name: (identifier) @name) @capture
    """,
    "javascript": """
    (function_declaration name: (identifier) @name) @capture
    (class_declaration name: (identifier) @name) @capture
    (lexical_declaration (variable_declarator name: (identifier) @name value: (arrow_function))) @capture
    """,
    "go": """
    (function_declaration name: (identifier) @name) @capture
    (type_declaration (type_spec name: (type_identifier) @name)) @capture
    """
}

# --- 核心索引逻辑 ---

def extract_chunks(file_path: Path, language: str) -> List[Dict[str, Any]]:
    """使用 Tree-sitter 从单个文件中提取代码块。"""
    try:
        parser = get_parser(language)
        query_str = QUERIES.get(language)
        if not query_str:
            return []

        query = parser.language.query(query_str)
        code = file_path.read_text(encoding='utf-8')
        tree = parser.parse(bytes(code, "utf8"))

        chunks = []
        captures = query.captures(tree.root_node)

        for node, _ in captures:
            name_node = node.child_by_field_name("name")
            if name_node:
                block_name = name_node.text.decode('utf8')
            else:
                name_node = next((c for c in node.children if c.type == 'identifier'), None)
                block_name = name_node.text.decode('utf8') if name_node else "anonymous"

            block_code = node.text.decode('utf8')

            document = f"// FILEPATH: {file_path.relative_to(WORKSPACE_DIR)}\n" \
                       f"// NAME: {block_name}\n\n" \
                       f"{block_code}"

            chunk_id = f"{file_path.relative_to(WORKSPACE_DIR)}::{block_name}"

            chunks.append({
                "document": document,
                "metadata": {"filepath": str(file_path.relative_to(WORKSPACE_DIR)), "name": block_name},
                "id": chunk_id,
            })
        return chunks
    except Exception as e:
        print(f"解析 {file_path} 失败: {e}")
        if "was not built" in str(e):
            print("提示：请确保您的系统已安装 C 编译器（如 gcc）。")
        return []

def index_workspace():
    """遍历工作区，提取代码块，并将其索引到 ChromaDB 中。"""
    print("开始索引工作区...")
    client = chromadb.PersistentClient(path=str(DB_PATH))

    # 尝试删除旧集合，如果不存在则忽略错误
    try:
        client.delete_collection(name=COLLECTION_NAME)
        print(f"已删除旧集合: '{COLLECTION_NAME}'")
    except ValueError: # ChromaDB 在 0.4.x 版本中引发 ValueError
        pass
    except Exception: # 捕获其他可能的异常，以提高健壮性
        pass

    collection = client.create_collection(name=COLLECTION_NAME)

    all_chunks = []
    for file_path in WORKSPACE_DIR.rglob('*'):
        if file_path.is_file() and file_path.suffix in LANGUAGES:
            language = LANGUAGES[file_path.suffix]
            print(f"正在处理: {file_path.relative_to(WORKSPACE_DIR)} ({language})")
            chunks = extract_chunks(file_path, language)
            all_chunks.extend(chunks)

    if not all_chunks:
        print("未找到可索引的代码块。")
        return

    print(f"正在为 {len(all_chunks)} 个代码块生成嵌入...")
    embeddings = model.encode([chunk['document'] for chunk in all_chunks]).tolist()

    collection.add(
        documents=[chunk['document'] for chunk in all_chunks],
        metadatas=[chunk['metadata'] for chunk in all_chunks],
        ids=[chunk['id'] for chunk in all_chunks],
        embeddings=embeddings
    )
    print(f"索引完成。总共索引了 {len(all_chunks)} 个代码块。")

def retrieve_context(query: str, n_results: int = 3) -> List[str]:
    """根据查询从 ChromaDB 检索相关的代码上下文。"""
    print(f"正在为查询检索上下文: '{query}'")
    client = chromadb.PersistentClient(path=str(DB_PATH))
    try:
        collection = client.get_collection(name=COLLECTION_NAME)
    except ValueError:
        print("数据库/集合不存在，无法检索。请先运行索引。")
        return []

    query_embedding = model.encode([query])[0].tolist()

    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=n_results
    )

    return results['documents'][0] if results and results.get('documents') else []

# --- 主程序入口 (用于手动测试) ---
if __name__ == '__main__':
    # 为了测试，我们先在工作区创建一些示例文件
    # 使用 parents=True 确保父目录（workspace）也会被创建
    (WORKSPACE_DIR / "math").mkdir(parents=True, exist_ok=True)

    # Python 示例
    (WORKSPACE_DIR / "math/operations.py").write_text("""
class Calculator:
    def add(self, a, b):
        return a + b

def subtract(a, b):
    return a - b
    """)

    # JavaScript 示例
    (WORKSPACE_DIR / "utils.js").write_text("""
function greet(name) {
    console.log(`Hello, ${name}!`);
}
const sayGoodbye = (name) => {
    console.log(`Goodbye, ${name}.`);
}
    """)

    # 1. 索引工作区
    index_workspace()

    # 2. 检索示例
    print("\n--- 检索测试 ---")
    retrieved_docs = retrieve_context("a function to add two numbers")
    print("\n检索到的文档:")
    if retrieved_docs:
        for doc in retrieved_docs:
            print("---")
            print(doc)
    else:
        print("未检索到任何文档。")