import pytest
from unittest.mock import MagicMock, patch

# New function added by MiniJules
# 模拟ChromaDB以隔离测试
with patch('autogen_ext.memory.chromadb.ChromaDBVectorMemory', MagicMock()):
    import minijules.tools as tools
    from minijules.app import JulesApp
    from minijules.types import TaskState

@pytest.fixture
def app_instance(mocker):
    """提供一个模拟了核心依赖的 JulesApp 实例。"""
    mocker.patch('minijules.app.create_core_agent')
    app = JulesApp(task_string="test task", config_list=[{'model': 'mock'}])
    return app

# New function added by MiniJules
@pytest.mark.asyncio
async def test_set_plan_tool(app_instance):
    """测试 set_plan 工具是否能正确更新应用状态。"""
    # 1. 准备
    test_plan = "1. 第一步\n2. 第二步"

    # 2. 执行
    # 调用 app 实例上的方法，而不是 tools 模块中的函数
    result = app_instance.set_plan(test_plan)

    # 3. 验证
    assert app_instance.state.plan == test_plan
    assert app_instance.state.current_step_index == 1
    assert "计划已成功设置" in result
    assert "1/2" in result

# New function added by MiniJules
@pytest.mark.asyncio
async def test_record_user_approval_for_plan_tool(app_instance):
    """测试 record_user_approval_for_plan 工具。"""
    # 前置条件：必须先有一个计划
    app_instance.state.plan = "1. 一个计划"

    result = app_instance.record_user_approval_for_plan()

    assert app_instance.state.plan_approved is True
    assert "计划已获批准" in result

# New function added by MiniJules
@pytest.mark.asyncio
async def test_plan_step_complete_tool(app_instance):
    """测试 plan_step_complete 工具的逻辑。"""
    # 前置条件
    app_instance.state.plan = "1. 步骤一\n2. 步骤二"
    app_instance.state.plan_approved = True
    app_instance.state.current_step_index = 1

    # 执行第一步
    result1 = app_instance.plan_step_complete("完成了第一步")

    assert app_instance.state.current_step_index == 2
    assert "步骤已完成。下一步: 2/2" in result1

    # 执行第二步
    result2 = app_instance.plan_step_complete("完成了第二步")

    assert app_instance.state.current_step_index == 2 # 到达最后一步后不应再增加
    assert "所有计划步骤均已完成" in result2

# New function added by MiniJules
@pytest.mark.asyncio
async def test_plan_step_complete_requires_approval(app_instance):
    """测试 plan_step_complete 在计划未批准时是否会正确返回错误。"""
    app_instance.state.plan = "1. 某计划"
    app_instance.state.plan_approved = False # 关键：计划未批准

    result = app_instance.plan_step_complete("试图完成")

    assert "错误: 计划尚未获得用户批准" in result