"""V1 本地 Web UI（FastAPI）。

这里刻意保持为空、不做任何预导入。

``python -m src.api.app`` 会先导入本包、再执行 app 模块；如果在这个文件里
写 ``from .app import app``，模块会在 sys.modules 里出现两次并抛出
RuntimeWarning。需要 app 实例时请直接 ``from src.api.app import app``。
"""
