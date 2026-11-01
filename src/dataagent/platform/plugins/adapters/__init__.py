"""内置引擎适配器（P0：engine.pdf / engine.csv / engine.text）。

`builtin_plugins()` 返回可直接注册进 PluginRegistry 的同进程插件实例。
"""

from __future__ import annotations

from dataagent.contracts.plugin import Plugin
from dataagent.platform.plugins.adapters.engine_csv import PLUGIN as CSV_PLUGIN
from dataagent.platform.plugins.adapters.engine_pdf import PLUGIN as PDF_PLUGIN
from dataagent.platform.plugins.adapters.engine_text import PLUGIN as TEXT_PLUGIN


def builtin_plugins() -> tuple[Plugin, ...]:
    return (PDF_PLUGIN, CSV_PLUGIN, TEXT_PLUGIN)
