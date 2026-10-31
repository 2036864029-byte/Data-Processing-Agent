"""进程入口层（依赖 = 全部）。

CLI（唯一入口）、HTTP API（无状态 + /status 只读页）、worker（数据面批执行进程壳）。
"""

__version__ = "0.1.0"
