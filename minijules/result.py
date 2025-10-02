from dataclasses import dataclass

@dataclass
class ToolExecutionResult:
    """
    一个标准化的数据结构，用于封装工具执行的结果。

    Attributes:
        success: 一个布尔值，表示工具执行是否成功。
        result: 一个字符串，包含工具的输出（如果成功）或错误信息（如果失败）。
    """
    success: bool
    result: str