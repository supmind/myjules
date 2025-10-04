# MiniJules: 一个基于 AutoGen v0.4+ 的自主 AI 开发助手

MiniJules 是一个基于 `autogen` 框架（v0.4+）构建的高级 AI 软件工程师。它采用了一个由群聊（Group Chat）驱动的架构，使其能够严谨、迭代地完成复杂的软件开发任务。

## 核心架构

项目的核心是 `autogen` 的 `RoundRobinGroupChat`，它负责协调两个关键代理之间的对话：

1.  **`CoreAgent` (核心代理)**:
    *   **角色**: 项目的“大脑”，一个 `AssistantAgent`。
    *   **职责**: 接收任务目标和历史记录，然后决定下一步要执行的工具调用。它的系统提示经过精心设计，以引导其遵循测试驱动开发（TDD）流程和版本控制最佳实践。

2.  **`CodeExecutorAgent` (代码执行代理)**:
    *   **角色**: 项目的“双手”，一个 `CodeExecutorAgent`。
    *   **职责**: 接收来自 `CoreAgent` 的代码块（如 Python 脚本或 shell 命令）并执行它们，然后将执行结果（STDOUT, STDERR, 返回码）返回给 `CoreAgent`。

这种架构使得 `CoreAgent` 可以专注于“思考”，而 `CodeExecutorAgent` 则负责可靠地“行动”。

## 关键特性

-   **网络搜索能力**: MiniJules 现在可以访问互联网！当遇到未知错误、不熟悉的技术或需要查阅最新文档时，它会主动使用 `google_search` 和 `view_text_website` 工具来寻找答案，显著增强了其自主解决问题的能力。
-   **`AGENTS.md` 协议**: 您可以在项目的根目录中创建一个 `AGENTS.md` 文件，用以定义项目特定的规则、编码规范或任何其他重要指令。MiniJules 在任务开始时会首先阅读此文件，并在整个工作流程中严格遵守这些指南，使其能更好地适应不同项目的独特要求。
-   **高级RAG (检索增强生成)**: 系统内置了一个基于 `ChromaDB` 和 `sentence-transformers` 的代码检索系统 (`code_rag_memory`)。在任务开始时，它会自动索引工作区中的代码，使 `CoreAgent` 能够基于现有代码的上下文做出更明智的决策。
-   **长期记忆**: 系统拥有一个独立的记忆库 (`task_history_memory`)，用于存储已完成任务的总结和代码变更。这使得 `CoreAgent` 能够从过去的成功经验中学习。
-   **全面的工具集**: `CoreAgent` 可以使用 `minijules/tools.py` 中提供的一系列强大工具，包括安全的文件操作、精确的代码编辑、Shell 命令执行和版本控制。
-   **自动化代码审查**: 在提交最终工作前，`CoreAgent` 会调用一个 LLM 来对代码变更进行评审，以确保质量。

## 安装

1.  克隆此仓库。
2.  确保您已安装 Python 3.9+。
3.  强烈建议创建一个虚拟环境以避免依赖冲突。
    ```bash
    python3 -m venv .venv
    source .venv/bin/activate
    ```
4.  **安装依赖**: 项目使用 `pip-tools` 管理依赖。所有必需的依赖项都已固定在 `requirements.txt` 中。请运行以下命令进行安装：
    ```bash
    python3 -m pip install -r requirements.txt
    ```

## 如何运行

通过命令行启动 MiniJules，并向其分配一个任务。

**重要提示**: 如果您创建了 `AGENTS.md` 文件，MiniJules 会自动读取并遵守其中的规则。

**基本用法:**
```bash
python3 -m minijules.app "您的任务描述"
```

**示例:**
```bash
python3 -m minijules.app "在 a.py 中创建一个名为 'add' 的函数，它接受两个参数并返回它们的和，并为它编写一个 pytest 测试。"
```

**设置最大步骤数:**
您可以使用 `--max-steps` 标志来限制对话的最大轮次。
```bash
python3 -m minijules.app "您的任务描述" --max-steps 50
```

## 如何运行测试

项目使用 `pytest` 进行测试。要运行测试套件，请在项目根目录下执行：
```bash
python3 -m pytest
```

## 配置

-   **语言模型 (LLM)**:
    *   在项目根目录下创建一个 `.env` 文件（可以从 `.env.template` 复制）。
    *   在 `.env` 文件中，设置您的 `OAI_CONFIG_LIST` 环境变量。这是一个包含您的 LLM 提供商凭据的 JSON 字符串。请参考 `autogen` 文档了解其格式。
-   **语言支持**:
    *   代码解析和分块的语言特定配置位于 `minijules/language_config.json` 文件中。您可以编辑此文件以调整或扩展对新语言的支持。