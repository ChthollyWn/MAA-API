from maa_api.model.response import ResponseCode

class ResponseException(Exception):
    def __init__(self, message: str, code: int = ResponseCode.BAD_REQUEST.value):
        self.code = code
        self.message = message