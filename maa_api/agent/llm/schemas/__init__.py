"""结构化输出的业务 schema（M67 规格 §4.2.7）。

本里程碑没有业务 schema：注册表与 ``register_schema`` 等入口在
:mod:`maa_api.agent.llm.structured`。M70 起每个 schema 一个 pydantic 模型文件放在
本包下，模块末尾调用 ``register_schema``，再在这里逐个导入以触发注册。未来导入
位置如下（现在没有可导入的模块，所以整段保持注释）：

    from maa_api.agent.llm.schemas import compaction_summary  # noqa: F401
"""
