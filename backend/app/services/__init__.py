"""业务服务层。

规则：本层可以 import repositories、models、core，但**不 import fastapi**。
领域异常定义在 `core/exceptions.py`，由 api 层的异常处理器翻译成 HTTP——
因此 service 可以脱离 HTTP 直接单测。
"""
