"""``tests/api``：接入层用例（M3-04 起）。

本目录的用例**不得 import ``maa_api.main``**（M3-09 之前它还是旧装配），
被测 app 一律在用例里用 ``FastAPI()`` 现搭；公共夹具见 :mod:`tests.api.conftest`。
"""
