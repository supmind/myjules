import os
import unittest
from pathlib import Path
import shutil

# 将 minijules 目录添加到 sys.path 以便导入
import sys
sys.path.insert(0, str(Path(__file__).parent.resolve()))

import minijules.tools as tools
import minijules.indexing as indexing

class TestTools(unittest.TestCase):
    """测试集，用于验证 minijules/tools.py 中的函数。"""

    def setUp(self):
        """在每个测试前设置环境。"""
        self.workspace = tools.WORKSPACE_DIR
        # 确保我们从一个干净的工作区开始
        if self.workspace.exists():
            shutil.rmtree(self.workspace)
        self.workspace.mkdir()
        os.chdir(self.workspace) # 更改当前目录以便于路径操作

    def tearDown(self):
        """在每个测试后清理环境。"""
        os.chdir(Path(__file__).parent.resolve()) # 切换回原始目录
        if self.workspace.exists():
            shutil.rmtree(self.workspace)

    def test_create_and_delete_file(self):
        """测试文件的创建和删除功能。"""
        print("\n正在测试: test_create_and_delete_file")
        filename = "test_delete.txt"
        content = "这个文件将被删除。"

        # 创建文件
        create_result = tools.create_file(filename, content)
        self.assertIn("已成功创建", create_result)
        self.assertTrue((self.workspace / filename).is_file(), "文件应已创建")

        # 删除文件
        delete_result = tools.delete_file(filename)
        self.assertIn("已成功删除", delete_result)
        self.assertFalse((self.workspace / filename).is_file(), "文件应已被删除")
        print("✅ 通过")

    def test_replace_code_block(self):
        """测试替换代码块的功能。"""
        print("\n正在测试: test_replace_code_block")
        filename = "test_replace.txt"
        search_block = "=== old_code ==="
        replace_block = "=== new_code ==="
        original_content = f"line1\n{search_block}\nline3"
        expected_content = f"line1\n{replace_block}\nline3"

        # 创建文件
        tools.create_file(filename, original_content)

        # 替换代码块
        replace_result = tools.replace_code_block(filename, search_block, replace_block)
        self.assertIn("已成功替换", replace_result)

        # 验证内容
        new_content = tools.read_file(filename)
        self.assertEqual(new_content, expected_content, "代码块应已被替换")
        print("✅ 通过")

    def test_scratchpad(self):
        """测试便签的写入和读取功能。"""
        print("\n正在测试: test_scratchpad")
        # 确保便签是空的
        (self.workspace / ".scratchpad.md").unlink(missing_ok=True)

        # 读取空的便签
        read_result1 = tools.read_scratchpad()
        self.assertIn("为空或不存在", read_result1)

        # 写入便签
        content1 = "第一行笔记"
        write_result1 = tools.write_to_scratchpad(content1)
        self.assertIn("已成功写入", write_result1)

        # 读取便签
        read_result2 = tools.read_scratchpad()
        self.assertEqual(read_result2.strip(), content1)

        # 追加内容到便签
        content2 = "第二行笔记"
        tools.write_to_scratchpad(content2)

        # 再次读取便签
        read_result3 = tools.read_scratchpad()
        self.assertIn(content1, read_result3)
        self.assertIn(content2, read_result3)
        print("✅ 通过")

def run_indexing_tests():
    """运行 indexing.py 中内置的测试。"""
    print("\n" + "="*20)
    print("正在运行 indexing.py 内置测试...")
    print("="*20)

    # --- 设置测试环境 ---
    (indexing.WORKSPACE_DIR / "math").mkdir(parents=True, exist_ok=True)
    (indexing.WORKSPACE_DIR / "math/operations.py").write_text("""
class Calculator:
    def add(self, a, b):
        return a + b
    """)

    # --- 运行索引 ---
    indexing.index_workspace()

    # --- 验证检索 ---
    retrieved_docs = indexing.retrieve_context("calculator add function", n_results=1)

    print("\n检索到的文档:")
    if retrieved_docs and retrieved_docs[0]:
        doc = retrieved_docs[0]
        print("---")
        print(doc)
        assert "// CLASS: Calculator" in doc, "验证失败: 未找到类上下文"
        assert "def add" in doc, "验证失败: 未找到函数定义"
        print("\n✅ indexing.py 测试验证成功。")
    else:
        print("\n❌ indexing.py 测试验证失败。")
        raise AssertionError("indexing.py 检索测试失败")


if __name__ == "__main__":
    print("="*20)
    print("开始运行测试套件...")
    print("="*20)

    # 运行工具的单元测试
    suite = unittest.TestSuite()
    suite.addTest(unittest.makeSuite(TestTools))
    runner = unittest.TextTestRunner()
    result = runner.run(suite)

    if not result.wasSuccessful():
        print("\n❌ 工具测试失败，终止执行。")
        sys.exit(1)

    # 如果工具测试通过，则运行索引测试
    try:
        run_indexing_tests()
    except Exception as e:
        print(f"\n❌ 索引测试发生错误: {e}")
        sys.exit(1)

    print("\n" + "="*20)
    print("🎉 所有测试均已成功通过！")
    print("="*20)