"""
资产管理系统 —— 后端应用包。

分层结构：
    app.api     接口层（路由 + 依赖注入）
    app.core    基础设施（配置/安全/响应/异常/日志）
    app.crud    数据访问层
    app.db      数据库引擎与会话
    app.models  ORM 模型
    app.schemas Pydantic 校验模型
    app.utils   通用工具
"""

__version__ = "1.0.0"
