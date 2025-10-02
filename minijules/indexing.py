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
COLLECTION_NAME = "code_index"

# 加载句子转换器模型 (已升级)
model = SentenceTransformer('BAAI/bge-large-en-v1.5')

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
        lang_obj = get_language(language) # query需要语言对象

        query_str = QUERIES.get(language)
        if not query_str:
            return []

        query = lang_obj.query(query_str)
        code = file_path.read_text(encoding='utf-8')
        tree = parser.parse(bytes(code, "utf8"))

        chunks = []
        captures = query.captures(tree.root_node)

        # 正确的迭代方式：直接访问与'@capture'键关联的节点列表
        for node in captures.get('capture', []):
            # 提取块名称
            name_node = node.child_by_field_name("name")
            if name_node:
                block_name = name_node.text.decode('utf8')
            else: # 备用方案，适用于 JS 箭头函数等情况
                name_node = next((c for c in node.children if c.type == 'identifier'), None)
                block_name = name_node.text.decode('utf8') if name_node else "anonymous"

            block_code = node.text.decode('utf8')

            # --- 新增：向上查找父级上下文 ---
            parent_class_name = None
            current_node = node.parent
            while current_node:
                if current_node.type in ["class_definition", "class_declaration"]:
                    # 找到父类的名称节点
                    class_name_node = current_node.child_by_field_name("name")
                    if class_name_node:
                        parent_class_name = class_name_node.text.decode('utf8')
                        break # 找到最近的父类即可
                current_node = current_node.parent

            # --- 构建文档和元数据 ---
            document_lines = [f"// FILEPATH: {file_path.relative_to(WORKSPACE_DIR)}"]
            metadata = {"filepath": str(file_path.relative_to(WORKSPACE_DIR))}

            if parent_class_name:
                document_lines.append(f"// CLASS: {parent_class_name}")
                metadata["class"] = parent_class_name

            document_lines.append(f"// NAME: {block_name}")
            metadata["name"] = block_name

            document_lines.append(f"\n{block_code}")
            document = "\n".join(document_lines)

            # 创建唯一的 ID
            chunk_id = f"{file_path.relative_to(WORKSPACE_DIR)}::{parent_class_name}::{block_name}" if parent_class_name else f"{file_path.relative_to(WORKSPACE_DIR)}::{block_name}"

            chunks.append({
                "document": document,
                "metadata": metadata,
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

    try:
        client.delete_collection(name=COLLECTION_NAME)
        print(f"已删除旧集合: '{COLLECTION_NAME}'")
    except ValueError:
        pass
    except Exception:
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

if __name__ == '__main__':
    # --- 设置测试环境 ---
    (WORKSPACE_DIR / "math").mkdir(parents=True, exist_ok=True)

    # Python 测试文件，包含一个类和一个独立函数
    (WORKSPACE_DIR / "math/operations.py").write_text("""
class Calculator:
    \"\"\"一个简单的计算器类。\"\"\"
    def add(self, a, b):
        \"\"\"将两个数相加。\"\"\"
        return a + b

    def multiply(self, a, b):
        \"\"\"将两个数相乘。\"\"\"
        return a * b

def subtract(a, b):
    \"\"\"一个独立的减法函数。\"\"\"
    return a - b
    """)

    # JavaScript 测试文件
    (WORKSPACE_DIR / "utils.js").write_text("""
class Greeter {
    constructor(name) {
        this.name = name;
    }

    greet() {
        console.log(`Hello, ${this.name}!`);
    }
}
const sayGoodbye = (name) => {
    console.log(`Goodbye, ${name}.`);
}
    """)

    # --- 运行索引 ---
    index_workspace()

    # --- 验证检索 ---
    print("\n--- 检索测试：查询类方法 ---")
    # 这个查询现在应该能利用类上下文
    retrieved_docs = retrieve_context("calculator add function", n_results=1)

    print("\n检索到的文档:")
    if retrieved_docs and retrieved_docs[0]:
        doc = retrieved_docs[0]
        print("---")
        print(doc)
        # 验证是否包含了类上下文
        if "// CLASS: Calculator" in doc and "def add" in doc:
            print("\n✅ 验证成功: 检索到的方法包含了正确的类上下文。")
        else:
            print("\n❌ 验证失败: 未找到预期的类上下文。")
    else:
        print("未检索到任何文档。")

    print("\n--- 检索测试：查询独立函数 ---")
    retrieved_docs_standalone = retrieve_context("subtract two numbers", n_results=1)

    print("\n检索到的文档:")
    if retrieved_docs_standalone and retrieved_docs_standalone[0]:
        doc = retrieved_docs_standalone[0]
        print("---")
        print(doc)
        if "def subtract" in doc and "// CLASS:" not in doc:
             print("\n✅ 验证成功: 独立函数被正确检索，且不包含类上下文。")
        else:
            print("\n❌ 验证失败: 独立函数检索结果不正确。")
    else:
        print("未检索到任何文档。")