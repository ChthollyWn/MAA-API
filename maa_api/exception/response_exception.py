from maa_api.model.response import ResponseCode

class ResponseException(Exception):
    def __init__(self, code: int = ResponseCode.BAD_REQUEST.value, message: str = "Bad Request"):
        self.code = code
        self.message = message