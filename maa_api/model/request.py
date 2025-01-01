from pydantic import BaseModel

class TaskRequest(BaseModel):
    name: str

    """启动关闭游戏客户端"""
    client_type: str | None
    account_name: str | None
