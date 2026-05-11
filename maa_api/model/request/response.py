from pydantic import BaseModel
from typing import Any
from enum import Enum

class ResponseCode(Enum):
    SUCCESS = 10200
    BAD_REQUEST = 10400
    UNAUTHORIZED = 10401
    FORBIDDEN = 10403
    NOT_FOUND = 10404
    SERVER_ERROR = 50000

class Response(BaseModel):
    code: int
    message: str
    data: Any = None

    @classmethod
    def build(cls, code: int, message: str, data: Any = None) -> "Response":
        return cls(code=code, message=message, data=data)

    @classmethod
    def success(cls, message: str = "success", data: Any = None) -> "Response":
        return cls(code=ResponseCode.SUCCESS.value, message=message, data=data)

    @classmethod
    def bad_request(cls, message: str = "bad request", data: Any = None) -> "Response":
        return cls(code=ResponseCode.BAD_REQUEST.value, message=message, data=data)

    @classmethod
    def unauthorized(cls, message: str = "unauthorized", data: Any = None) -> "Response":
        return cls(code=ResponseCode.UNAUTHORIZED.value, message=message, data=data)

    @classmethod
    def forbidden(cls, message: str = "forbidden", data: Any = None) -> "Response":
        return cls(code=ResponseCode.FORBIDDEN.value, message=message, data=data)

    @classmethod
    def not_found(cls, message: str = "not found", data: Any = None) -> "Response":
        return cls(code=ResponseCode.NOT_FOUND.value, message=message, data=data)

    @classmethod
    def server_error(cls, message: str = "server error", data: Any = None) -> "Response":
        return cls(code=ResponseCode.SERVER_ERROR.value, message=message, data=data)