from maa_api.config.config import Config
from maa_api.model.request.response import ResponseCode
from maa_api.exception.response_exception import ResponseException

def token_auth(token: str = ""):
    access_token = Config.get_config("app", "access_token")
    if access_token and access_token != token:
        raise ResponseException(code=ResponseCode.UNAUTHORIZED.value, message="unauthorized")