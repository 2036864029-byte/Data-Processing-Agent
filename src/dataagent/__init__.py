"""DataAgent — 插件化异构数据处理 Agent。

确定性流水线：页级决策全部规则，任务级模型调用 ≤ 3 处（PRD V2.3）。
5 层包结构依赖方向单向：apps → pipeline → kernel → platform → contracts。
"""

__version__ = "0.1.0"
