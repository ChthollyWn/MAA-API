# M3-01 方案前置实测结论：pydantic 判别联合、x-* schema 导出与最小 FastAPI 装配

本文件由 `scripts/probe_api_skeleton.py` 生成，全部结论来自本机实测（脚本内定义的探针模型与探针 app，不依赖网络、内核、真实设备与仓库真实库文件）。原始数据见 `tests/fixtures/api_probe_result.json`。

实测环境：Python 3.13.3 / pydantic 2.11.10 / fastapi 0.141.1 / starlette 1.6.0 / httpx 0.27.2 / macOS-15.4.1-arm64-arm-64bit-Mach-O

## 必读结论

### 1. 400 与 422 的可编程判据（`api/errors.py` 直接照抄）

判据是 `RequestValidationError.errors()` 里每一项的 **`type` 字符串**，不是状态码也不是 `msg`：

| 实测 `type` | 触发情形 | 实测 FastAPI 出厂行为 | 建议 |
|---|---|---|---|
| `union_tag_invalid` | discriminator 取值不在已知任务类型内（实测 `union_tag_invalid` loc=[] ctx=['discriminator', 'expected_tags', 'tag']） | 422 | **400 `UNKNOWN_TASK_TYPE`**（docs/05 §2.2 要求） |
| `json_invalid` | 请求体不是合法 JSON（`loc=('body', <int>)`） | 422 | **400 `MALFORMED_JSON`**（docs/05 §4.2 要求；**出厂行为是 422，必须特判**） |
| `extra_forbidden` | `extra="forbid"` 下的多余字段 | 422 | 422 `VALIDATION_ERROR` |
| `int_parsing` / `value_error` / `union_tag_not_found` / `too_short` / `list_type` | 字段类型、跨字段规则、缺 discriminator、list 长度 | 422 | 422 `VALIDATION_ERROR` |

**缺 discriminator 与未知 discriminator 不是同一个 `type`**：实测分别是 `union_tag_not_found` 与 `union_tag_invalid`（见下表）。建议只把 `union_tag_invalid` 判成 400；`union_tag_not_found` 是「必填的 discriminator 缺失」，与其它缺字段同类，留在 422，前端可高亮类型选择器。

可直接照抄的映射函数（探针里那份就是它，`classify_validation_errors`）：

```python
STATUS_BY_ERROR_TYPE = {
    "union_tag_invalid": (400, "UNKNOWN_TASK_TYPE"),
    "json_invalid": (400, "MALFORMED_JSON"),
}
DEFAULT_ERROR_STATUS = (422, "VALIDATION_ERROR")


def classify_validation_errors(errors: list[dict]) -> tuple[int, str]:
    for err in errors:
        loc = list(err.get("loc") or ())
        if err.get("type") == "union_tag_invalid":
            return 400, "UNKNOWN_TASK_TYPE"
        if err.get("type") == "json_invalid" and len(loc) == 2 and isinstance(loc[1], int):
            return 400, "MALFORMED_JSON"
    return 422, "VALIDATION_ERROR"
```

`json_invalid` 必须带 `loc` 形状判断：请求体本身解码失败时 `loc=('body', <int 字符偏移>)`，而字段级 JSON 解析失败是 `loc=('body', '<字段名>')` —— 只按 `type` 判会把后者的 422 误判成 400。

### 2. `AppError` 的接法：**A（校验器直接抛，app 级处理器接住）**

实测结论（两种路径都试过）：`model_validator` 里抛**非 ValueError 的自定义异常**时，pydantic 2.11 **不做任何包装**——`TypeAdapter.validate_python` 与 FastAPI 请求体校验都把它原样抛出：

- TypeAdapter 路径：`outcome=raised`，异常类型 `ProbeAppError`，`wrapped_in_validation_error=False`，随异常带出的 `extra={'risk': 'consume'}` 完整保留。
- FastAPI 路径：请求体校验函数（`fastapi/dependencies/utils.py::request_body_to_args` → `fastapi/_compat/v2.py` → `TypeAdapter.validate_python`）只 `except ValidationError`，自定义异常穿过路由层直达 `@app.exception_handler(ProbeAppError)`（实测 status=400，响应体 {"code": "TASK_PARAM_INVALID", "details": {"risk": "consume"}, "message": "mode=1 不允许（validator 抛非 ValueError）"}），`RequestValidationError` 处理器**没有**被调用。
- 对照：`ValueError` 被包装成 `ValidationError`，`type="value_error"`、`ctx` 里带 `error` 键且保留原实例（实测 `ctx_error_type=ValueError`）。

**一句话结论**：走 A。`api/errors.py` 只需注册 `AppError`（普通 `Exception` 子类）与 `RequestValidationError` 两个处理器；不要为了换 400 而去 `ctx.error` 里捞 AppError——那条路（B）只在 AppError 是 `ValueError` 子类时才存在，而实测证明根本不需要把 AppError 变成 ValueError。

附带一条实测坑：`ValueError` 的 `errors()` 里 `ctx.error` 是**异常实例**，`json.dumps(exc.errors())` 直接 `TypeError`（实测 `json_serializable=False`）。处理器里必须走 `fastapi.encoders.jsonable_encoder`（它把 `ctx.error` 渲染成 `{}`）或只挑 `type/loc/msg` 三键。

### 3. schema 导出（docs/05 §8.1）可直接照抄的三条

- `get_args(get_args(TaskInput)[0])` 稳定给出全部分支且**保持定义顺序**：["ProbeStartUpInput", "ProbeFightInput", "ProbeInfrastInput"]；`model_fields["name"].default` 就是类型名字符串：{"ProbeFightInput": "Fight", "ProbeInfrastInput": "Infrast", "ProbeStartUpInput": "StartUp"}。
- `model_json_schema(ref_template="#/$defs/{model}")` 只有嵌套模型时才出现顶层 `$defs`，`$ref` 形态实测为 `#/$defs/ProbeNestedSettings`；对象层的 `additionalProperties: false` 与 `properties` **同级**（实测 `additionalProperties=False`，`properties` 里没有它）。
- `x-*` 原样保留：实测 `series` 上保留了 ["x-enum-labels", "x-group", "x-label", "x-risk", "x-widget"]；只给 `validation_alias` 的字段在 schema 里会改用别名首选项做 property 名（`times` 字段在 schema 里叫 `Times`，上面挂着 ["x-depends-on", "x-group", "x-label"]）。

- ⚠️ docs/05 §7.2 的 `MaaField` 写不下 `x-depends-on` / `x-risk`：它的签名没有 `json_schema_extra` 入口，而 `**kwargs` 直接转给 `Field` 会撞车（实测 `pydantic.fields.Field() got multiple values for keyword argument 'json_schema_extra'`）。实现时必须给 `MaaField` 加一个额外 x-键参数（本探针叫 `x_extra`）。

### 4. `model_fields_set` 三态（docs/05 §9 的默认值注入）

| 提交 | `model_fields_set` | `model_dump(exclude_unset=True, by_alias=True, exclude={"name"})` | `to_core_params()`（`exclude_none=True`） |
|---|---|---|---|
| 不传字段 | ["stage"] | {"stage": "1-7"} | {"stage": "1-7"} |
| 显式传 null | ["dr_grandet", "stage"] | {"DrGrandet": null, "stage": null} | {} |
| 显式传值 | ["dr_grandet", "stage"] | {"DrGrandet": false, "stage": "CE-6"} | {"DrGrandet": false, "stage": "CE-6"} |

三态可区分，`normalize()` 可以照 docs/05 §9 写：`"client_type" not in task.model_fields_set` 判定「用户完全没提」，显式 `null` 会出现在 `model_fields_set` 里但被 `to_core_params()` 的 `exclude_none=True` 剔除。

### 5. 最小装配要点（docs/02 §7、docs/05 §2.5／§11）

- `redirect_slashes=False`：带尾斜杠实测 **404**；对照默认配置 **307** → `http://testserver/api/probe/no-slash`（`TestClient` 默认跟随重定向，跟随后的状态是 200，所以断言必须带 `follow_redirects=False`）。
- `generate_unique_id_function` 收到的 route 对象实测有 4 个、类型为 ["APIRoute", "_EffectiveRouteContext"]（`isinstance(route, APIRoute)` 实测取值 [false, true]）：`@app.get` 直挂的路由是 `APIRoute`，`include_router` 进来的路由在 fastapi 0.141 是 `_EffectiveRouteContext` 包装对象。两者都有 `.name` / `.tags`，docs/05 §11.4 的函数体可用；但**类型注解别写 `APIRoute`**，也不要对 route 做 `isinstance` 判断。
- `openapi_tags` 顺序原样进入 `app.openapi()["tags"]`：实测 ["system", "tasks", "pipelines"]；operationId 实测 {"get": "system_read_probe"}。
- `HTTPException(401, headers={"WWW-Authenticate": "Bearer"})` 头透传（实测 `www-authenticate=Bearer`）—— docs/05 §2 的 401 要求可直接满足。
- `@app.exception_handler(Exception)` + `TestClient(app, raise_server_exceptions=False)` 能拿到 500 响应体（实测 {"body": {"code": "INTERNAL_ERROR", "message": "kaboom", "type": "RuntimeError"}, "status_code": 500}）；`raise_server_exceptions` **默认 True 会直接把异常抛进测试**（实测 `RuntimeError`），本仓的 500 测试必须显式传 False。
- `lifespan` 在同步 `TestClient` 的 `with` 块里正常进入/退出（实测 ["enter", "exit"]），本仓不需要 `pytest-asyncio`；但**不用 context manager 时 lifespan 不执行**（实测 []）。

### 6. 与文档不一致 / 文档未覆盖的实测项

| # | 文档说法 | 实测 |
|---|---|---|
| 1 | docs/05 §2.2：请求体不是合法 JSON → 400 | FastAPI 出厂是 **422**，`type="json_invalid"`、`loc=("body", <int>)`；必须在 `RequestValidationError` 处理器里按 `type` 特判成 400 `MALFORMED_JSON` |
| 2 | docs/05 §2.2：discriminator 取值不在已知任务类型内 → 400 | 出厂是 **422**（`type="union_tag_invalid"`）；同样需要处理器特判。文档给的是目标行为，不是框架默认行为 |
| 3 | docs/05 §7.2 的 `MaaField` 能承载 §8.2 的 `x-depends-on` | 不能，签名与 `**kwargs` 冲突（TypeError），需加参数 |
| 4 | docs/05 §7.3：`validation_alias=AliasChoices("DrGrandet", "dr_grandet")` 的字段「两种写法都能提交」 | 成立；但**只给 `validation_alias` 不给 `serialization_alias`** 时 schema 属性名会变成别名首选项（实测 `times` 的 property 名是 `Times`（`times` 已不在 properties 里），`model_dump(by_alias=True)` 却仍输出 `times`）—— 两者分叉。要一致必须同时给 `serialization_alias`（`DrGrandet` 就是这样，两处都是 `DrGrandet`） |
| 5 | docs/02 §7：用 `lifespan` 替代 `on_event` | 实测 `on_event` 在 fastapi 0.141.1 仍可用但发 FastAPI deprecation 警告；`lifespan` + 同步 `TestClient` 可用，无需 `pytest-asyncio` |
| 6 | docs/05 §7.4：`PipelineCreate.tasks: list[TaskInput]` | 成立；联合嵌 list 时错误 `loc` 形如 ["body", "tasks", 0]，`union_tag_invalid` 依旧可在任意嵌套层级被 `type` 命中 |

另：`fastapi.testclient` 在本机会发 starlette 弃用警告，原文 [["StarletteDeprecationWarning", "Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead."], ["DeprecationWarning", "The anyio.abc.BlockingPortal alias is deprecated, use anyio.from_thread.BlockingPortal instead."]]（`httpx 0.27.2` 尚未换 `httpx2`）。测试里若要 `filterwarnings = error` 需先处理这两条。

## 1. A 组：判别联合与错误分界原始记录

### 1.1 `TypeAdapter.validate_python`（探针模型 `ProbeTaskInput`）

| 情形 | 结局 | 错误 `type` | 其余观测 |
|---|---|---|---|
| `unknown_tag` | validation_error | `union_tag_invalid` | {"ctx_error_type": [null], "ctx_keys": [["discriminator", "expected_tags", "tag"]], "exception": "ValidationError", "loc": [[]], "wrapped": null} |
| `missing_tag` | validation_error | `union_tag_not_found` | {"ctx_error_type": [null], "ctx_keys": [["discriminator"]], "exception": "ValidationError", "loc": [[]], "wrapped": null} |
| `extra_field` | validation_error | `extra_forbidden` | {"ctx_error_type": [null], "ctx_keys": [[]], "exception": "ValidationError", "loc": [["Alpha", "nope"]], "wrapped": null} |
| `field_type_error` | validation_error | `int_parsing` | {"ctx_error_type": [null], "ctx_keys": [[]], "exception": "ValidationError", "loc": [["Alpha", "count"]], "wrapped": null} |
| `validator_value_error` | validation_error | `value_error` | {"ctx_error_type": ["ValueError"], "ctx_keys": [["error"]], "exception": "ValidationError", "loc": [["Gamma"]], "wrapped": null} |
| `validator_app_error` | raised | — | {"ctx_error_type": [], "ctx_keys": [], "exception": "ProbeAppError", "loc": [], "wrapped": false} |
| `malformed_json` | validation_error | `json_invalid` | {"ctx_error_type": ["str"], "ctx_keys": [["error"]], "exception": "ValidationError", "loc": [[]], "wrapped": null} |

### 1.2 FastAPI 请求体（无自定义处理器的对照组，`raise_server_exceptions=False`）

| 情形 | 状态码 | 错误 `type` | 响应体（截断） |
|---|---|---|---|
| `unknown_tag` | 422 | `union_tag_invalid` | {"detail": [{"ctx": {"discriminator": "'name'", "expected_tags": "'Alpha', 'Beta', 'Gamma'", "tag": "Nope"}, "input": {"name": "Nope"}, "loc": ["body"], "msg":  |
| `missing_tag` | 422 | `union_tag_not_found` | {"detail": [{"ctx": {"discriminator": "'name'"}, "input": {}, "loc": ["body"], "msg": "Unable to extract tag using discriminator 'name'", "type": "union_tag_not |
| `extra_field` | 422 | `extra_forbidden` | {"detail": [{"input": 1, "loc": ["body", "Alpha", "nope"], "msg": "Extra inputs are not permitted", "type": "extra_forbidden"}]} |
| `field_type_error` | 422 | `int_parsing` | {"detail": [{"input": "abc", "loc": ["body", "Alpha", "count"], "msg": "Input should be a valid integer, unable to parse string as an integer", "type": "int_par |
| `validator_value_error` | 422 | `value_error` | {"detail": [{"ctx": {"error": {}}, "input": {"mode": 0, "name": "Gamma"}, "loc": ["body", "Gamma"], "msg": "Value error, mode=0 不允许（validator 抛 ValueError）", "t |
| `validator_app_error` | 500 | — | "Internal Server Error" |
| `malformed_json` | 422 | `json_invalid` | {"detail": [{"ctx": {"error": "Expecting property name enclosed in double quotes"}, "input": {}, "loc": ["body", 1], "msg": "JSON decode error", "type": "json_i |

### 1.3 `PipelineCreate.tasks: list[TaskInput]` 嵌套路径

| 情形 | 状态码 | 错误 `type` | 响应体（截断） |
|---|---|---|---|
| `pipeline_unknown_tag_in_list` | 422 | `union_tag_invalid` | {"detail": [{"ctx": {"discriminator": "'name'", "expected_tags": "'Alpha', 'Beta', 'Gamma'", "tag": "Nope"}, "input": {"name": "Nope"}, "loc": ["body", "tasks", |
| `pipeline_extra_field_in_list` | 422 | `extra_forbidden` | {"detail": [{"input": 1, "loc": ["body", "tasks", 0, "Alpha", "nope"], "msg": "Extra inputs are not permitted", "type": "extra_forbidden"}]} |
| `pipeline_empty_list` | 422 | `too_short` | {"detail": [{"ctx": {"actual_length": 0, "field_type": "List", "min_length": 1}, "input": [], "loc": ["body", "tasks"], "msg": "List should have at least 1 item |
| `pipeline_tasks_not_list` | 422 | `list_type` | {"detail": [{"input": "x", "loc": ["body", "tasks"], "msg": "Input should be a valid list", "type": "list_type"}]} |

### 1.4 按 `type` 映射 400/422 的处理器（推荐接法）

| 情形 | 状态码 | 响应体 |
|---|---|---|
| `unknown_tag` | 400 | {"code": "UNKNOWN_TASK_TYPE", "details": [{"ctx": {"discriminator": "'name'", "expected_tags": "'Alpha', 'Beta', 'Gamma'", "tag": "Nope"}, "input": {"name": "Nope"}, "loc": ["body"], "msg": "Input tag |
| `missing_tag` | 422 | {"code": "VALIDATION_ERROR", "details": [{"ctx": {"discriminator": "'name'"}, "input": {}, "loc": ["body"], "msg": "Unable to extract tag using discriminator 'name'", "type": "union_tag_not_found"}]} |
| `extra_field` | 422 | {"code": "VALIDATION_ERROR", "details": [{"input": 1, "loc": ["body", "Alpha", "nope"], "msg": "Extra inputs are not permitted", "type": "extra_forbidden"}]} |
| `field_type_error` | 422 | {"code": "VALIDATION_ERROR", "details": [{"input": "abc", "loc": ["body", "Alpha", "count"], "msg": "Input should be a valid integer, unable to parse string as an integer", "type": "int_parsing"}]} |
| `validator_value_error` | 422 | {"code": "VALIDATION_ERROR", "details": [{"ctx": {"error": {}}, "input": {"mode": 0, "name": "Gamma"}, "loc": ["body", "Gamma"], "msg": "Value error, mode=0 不允许（validator 抛 ValueError）", "type": "valu |
| `validator_app_error` | 400 | {"code": "TASK_PARAM_INVALID", "details": {"risk": "consume"}, "message": "mode=1 不允许（validator 抛非 ValueError）"} |
| `malformed_json` | 400 | {"code": "MALFORMED_JSON", "details": [{"ctx": {"error": "Expecting property name enclosed in double quotes"}, "input": {}, "loc": ["body", 1], "msg": "JSON decode error", "type": "json_invalid"}]} |
| `pipeline_unknown_tag_in_list`（嵌套） | 400 | {"code": "UNKNOWN_TASK_TYPE", "details": [{"ctx": {"discriminator": "'name'", "expected_tags": "'Alpha', 'Beta', 'Gamma'", "tag": "Nope"}, "input": {"name": "Nope"}, "loc": ["body", "tasks", 0], "msg" |
| `pipeline_extra_field_in_list`（嵌套） | 422 | {"code": "VALIDATION_ERROR", "details": [{"input": 1, "loc": ["body", "tasks", 0, "Alpha", "nope"], "msg": "Extra inputs are not permitted", "type": "extra_forbidden"}]} |
| `pipeline_empty_list`（嵌套） | 422 | {"code": "VALIDATION_ERROR", "details": [{"ctx": {"actual_length": 0, "field_type": "List", "min_length": 1}, "input": [], "loc": ["body", "tasks"], "msg": "List should have at least 1 item after vali |
| `pipeline_tasks_not_list`（嵌套） | 422 | {"code": "VALIDATION_ERROR", "details": [{"input": "x", "loc": ["body", "tasks"], "msg": "Input should be a valid list", "type": "list_type"}]} |

### 1.5 反向对照：`extra` 用默认 ignore

- `ProbeIgnoreAlpha.model_config["extra"] = null`（`None` 即 pydantic 默认的 ignore，未显式设置）；多余字段实测 `outcome=validated`、FastAPI 状态码 200。
- 同组字段类型错仍然被拦：状态码 422。→ 前面的 422 `extra_forbidden` 确实来自 `extra="forbid"`，不是别的原因。

### 1.6 validator 抛异常的两条路径对比

- `ValueError`：TypeAdapter `["value_error"]`（`ctx_keys=['error']`）；FastAPI 出厂 422。
- 自定义异常 `ProbeAppError`（MRO：["Exception", "BaseException", "object"]，`is_value_error_subclass=False`）：TypeAdapter `"raised"`、FastAPI 出厂对照 500（无处理器时是 starlette 的裸 500），推荐接法下 400。

## 2. B 组：x-* 与 JSON Schema 导出原始记录

### 2.1 `ProbeFightInput.model_json_schema(ref_template="#/$defs/{model}")`

- `top_level_keys` = ["additionalProperties", "properties", "title", "type"]
- `additional_properties` = false
- `additional_properties_in_properties` = false
- `has_defs` = false
- `properties` = ["DrGrandet", "Times", "drops", "enable", "medicine", "name", "series", "stage"]
- `x_keys_by_field` = {"DrGrandet": ["x-group", "x-label"], "Times": ["x-depends-on", "x-group", "x-label"], "drops": ["x-group", "x-label"], "enable": ["x-group", "x-label"], "medicine": ["x-group", "x-label"], "name": [], "series": ["x-enum-labels", "x-group", "x-label", "x-risk", "x-widget"], "stage": ["x-group", "x-label"]}
- `x_labels_verbatim` = {"DrGrandet": "节省理智碎石模式", "Times": "指定次数", "drops": "指定掉落数量", "enable": "启用本任务", "medicine": "最大理智药数", "name": null, "series": "连战次数", "stage": "关卡名"}

`series` 字段原文：

```json
{
  "anyOf": [
    {
      "maximum": 6,
      "minimum": -1,
      "type": "integer"
    },
    {
      "type": "null"
    }
  ],
  "default": null,
  "title": "Series",
  "x-enum-labels": {
    "-1": "禁用切换",
    "0": "自动选择最大可用次数",
    "1": "1 次"
  },
  "x-group": "基础",
  "x-label": "连战次数",
  "x-risk": "consume",
  "x-widget": "select"
}
```

`name` 字段原文：

```json
{
  "const": "Fight",
  "default": "Fight",
  "title": "Name",
  "type": "string"
}
```

`drops`（`dict[str, int]`）字段原文：

```json
{
  "anyOf": [
    {
      "additionalProperties": {
        "type": "integer"
      },
      "type": "object"
    },
    {
      "type": "null"
    }
  ],
  "default": null,
  "description": "key 为 item_id",
  "title": "Drops",
  "x-group": "高级",
  "x-label": "指定掉落数量"
}
```

### 2.2 嵌套模型的 `$defs` / `$ref`

- `top_level_keys` = ["$defs", "additionalProperties", "properties", "title", "type"]
- `defs_keys` = ["ProbeNestedSettings"]
- `nested_ref` = "#/$defs/ProbeNestedSettings"

### 2.3 `Literal` 数值枚举与 `ge`/`le`

`ProbeInfrastInput.mode`：

```json
{
  "anyOf": [
    {
      "enum": [
        0,
        10000,
        20000
      ],
      "type": "integer"
    },
    {
      "type": "null"
    }
  ],
  "default": null,
  "title": "Mode",
  "x-enum-labels": {
    "0": "默认换班",
    "10000": "自定义换班",
    "20000": "一键轮换"
  },
  "x-group": "基础",
  "x-label": "换班工作模式",
  "x-widget": "select"
}
```

`ProbeInfrastInput.threshold`：

```json
{
  "anyOf": [
    {
      "maximum": 1.0,
      "minimum": 0.0,
      "type": "number"
    },
    {
      "type": "null"
    }
  ],
  "default": null,
  "title": "Threshold",
  "x-group": "基础",
  "x-label": "工作心情阈值"
}
```

### 2.4 别名：schema property 名与 dump 键名

- `schema_property_names` = ["DrGrandet", "Times", "drops", "enable", "medicine", "name", "series", "stage"]
- `dr_grandet_schema_property_exists` = true
- `times_schema_property_exists` = true
- `times_python_property_exists` = false
- `validation_both_spellings_ok` = true
- `dump_by_alias_exclude_none_exclude_name` = {"DrGrandet": true, "stage": "1-7", "times": 3}
- `dump_by_alias_exclude_unset_exclude_name` = {"DrGrandet": true, "stage": "1-7", "times": 3}
- `dump_no_alias_exclude_unset` = {"dr_grandet": true, "stage": "1-7", "times": 3}
- `schema_by_alias_false_properties` = ["dr_grandet", "drops", "enable", "medicine", "name", "series", "stage", "times"]

### 2.5 `TaskInput` 的联合内省与两种 schema 导出对比

- `get_args_get_args_first_len` = 3
- `branches` = ["ProbeStartUpInput", "ProbeFightInput", "ProbeInfrastInput"]
- `name_defaults` = {"ProbeFightInput": "Fight", "ProbeInfrastInput": "Infrast", "ProbeStartUpInput": "StartUp"}
- `model_fields_name_default` = "Fight"
- `type_adapter_schema_top_keys` = ["$defs", "discriminator", "oneOf"]
- `type_adapter_discriminator` = {"mapping": {"Fight": "#/$defs/ProbeFightInput", "Infrast": "#/$defs/ProbeInfrastInput", "StartUp": "#/$defs/ProbeStartUpInput"}, "propertyName": "name"}
- `type_adapter_one_of_refs` = ["#/$defs/ProbeStartUpInput", "#/$defs/ProbeFightInput", "#/$defs/ProbeInfrastInput"]
- `type_adapter_defs_keys` = ["ProbeFightInput", "ProbeInfrastInput", "ProbeNestedSettings", "ProbeStartUpInput"]
- `type_adapter_has_top_level_properties` = false
- `per_model_top_keys` = {"ProbeFightInput": ["additionalProperties", "properties", "title", "type"], "ProbeInfrastInput": ["additionalProperties", "properties", "title", "type"], "ProbeStartUpInput": ["$defs", "additionalProperties", "properties", "title", "type"]}
- `per_model_has_discriminator` = {"ProbeFightInput": false, "ProbeInfrastInput": false, "ProbeStartUpInput": false}
- `default_ref_template_top_keys` = ["$defs", "discriminator", "oneOf"]
- `default_ref_template_ref` = "#/$defs/ProbeStartUpInput"

### 2.6 `model_fields_set` 三态

**字段缺失**（`missing`）

- `model_fields_set` = ["stage"]
- `dump_exclude_unset_by_alias` = {"stage": "1-7"}
- `dump_exclude_none_by_alias` = {"stage": "1-7"}
- `dump_default_by_alias` = {"DrGrandet": null, "drops": null, "enable": null, "medicine": null, "series": null, "stage": "1-7", "times": null}
- `to_core_params_like` = {"stage": "1-7"}

**显式传 null**（`explicit_null`）

- `model_fields_set` = ["dr_grandet", "stage"]
- `dump_exclude_unset_by_alias` = {"DrGrandet": null, "stage": null}
- `dump_exclude_none_by_alias` = {}
- `dump_default_by_alias` = {"DrGrandet": null, "drops": null, "enable": null, "medicine": null, "series": null, "stage": null, "times": null}
- `to_core_params_like` = {}

**显式传值**（`explicit_value`）

- `model_fields_set` = ["dr_grandet", "stage"]
- `dump_exclude_unset_by_alias` = {"DrGrandet": false, "stage": "CE-6"}
- `dump_exclude_none_by_alias` = {"DrGrandet": false, "stage": "CE-6"}
- `dump_default_by_alias` = {"DrGrandet": false, "drops": null, "enable": null, "medicine": null, "series": null, "stage": "CE-6", "times": null}
- `to_core_params_like` = {"DrGrandet": false, "stage": "CE-6"}

### 2.7 `MaaField` 与 `x_extra` 逃生口

- `path` = "docs/05 §7.2 MaaField(**kwargs 里带 json_schema_extra)"
- `collision_reproduced` = true
- `collision_message` = "pydantic.fields.Field() got multiple values for keyword argument 'json_schema_extra'"

### 2.8 `model_validator` 跨字段规则（`ProbeInfrastInput`）

```json
{
  "trigger": {
    "case": "infrast_cross_field",
    "error_types": [
      "value_error"
    ],
    "errors": [
      {
        "ctx_error_type": "ValueError",
        "ctx_keys": [
          "error"
        ],
        "input_present": true,
        "loc": [],
        "msg": "Value error, 自定义换班模式（mode=10000）必须提供 threshold",
        "type": "value_error",
        "url_present": true
      }
    ],
    "exception": "ValidationError",
    "input": {
      "mode": 10000,
      "name": "Infrast"
    },
    "json_serializable": false,
    "outcome": "validation_error",
    "path": "TypeAdapter.validate_python",
    "raw_errors": [
      {
        "ctx": {
          "error": "<ValueError>"
        },
        "input": {
          "mode": 10000,
          "name": "Infrast"
        },
        "loc": [],
        "msg": "Value error, 自定义换班模式（mode=10000）必须提供 threshold",
        "type": "value_error"
      }
    ]
  }
}
```


## 3. C 组：最小 FastAPI 装配原始记录

### 3.1 尾斜杠

- `disabled_status` = 404
- `no_slash_status` = 200
- `control_default_status` = 307
- `control_location` = "http://testserver/api/probe/no-slash"
- `control_followed_status` = 200

### 3.2 operationId 与 route 对象

route 对象实测（每条路由一条，共 4 条，类型集合 ["APIRoute", "_EffectiveRouteContext"]）：

```json
[
  {
    "has_name": true,
    "has_tags": true,
    "is_annotated_as_api_route_in_docs": true,
    "is_api_route_instance": true,
    "methods": [
      "GET"
    ],
    "name": "ping",
    "object_module": "fastapi.routing",
    "object_type": "APIRoute",
    "path": "/ping",
    "tags": []
  },
  {
    "has_name": true,
    "has_tags": true,
    "is_annotated_as_api_route_in_docs": true,
    "is_api_route_instance": false,
    "methods": [
      "GET"
    ],
    "name": "read_probe",
    "object_module": "fastapi.routing",
    "object_type": "_EffectiveRouteContext",
    "path": "/api/probe/no-slash",
    "tags": [
      "system"
    ]
  },
  {
    "has_name": true,
    "has_tags": true,
    "is_annotated_as_api_route_in_docs": true,
    "is_api_route_instance": false,
    "methods": [
      "GET"
    ],
    "name": "unauthorized",
    "object_module": "fastapi.routing",
    "object_type": "_EffectiveRouteContext",
    "path": "/api/probe/unauthorized",
    "tags": [
      "system"
    ]
  },
  {
    "has_name": true,
    "has_tags": true,
    "is_annotated_as_api_route_in_docs": true,
    "is_api_route_instance": false,
    "methods": [
      "GET"
    ],
    "name": "boom",
    "object_module": "fastapi.routing",
    "object_type": "_EffectiveRouteContext",
    "path": "/api/probe/boom",
    "tags": [
      "system"
    ]
  }
]
```

operationId 实测：{"/api/probe/boom": {"get": "system_boom"}, "/api/probe/no-slash": {"get": "system_read_probe"}, "/api/probe/unauthorized": {"get": "system_unauthorized"}, "/ping": {"get": "default_ping"}}

### 3.3 tag 顺序

`app.openapi()["tags"]` 顺序：["system", "tasks", "pipelines"]

### 3.4 HTTPException 与异常处理器

- `status_code` = 401
- `www_authenticate` = "Bearer"
- `body` = {"detail": "token 缺失或不匹配"}

- `status_code` = 500
- `body` = {"code": "INTERNAL_ERROR", "message": "kaboom", "type": "RuntimeError"}

- `raised` = true
- `exception` = "RuntimeError"
- `message` = "kaboom"

### 3.5 lifespan 与 deprecation 警告

- `log_inside_context` = ["enter"]
- `log_after_context` = ["enter", "exit"]
- `entered` = true
- `exited` = true
- `inside_request_status` = 200
- `no_context_manager` = {"entered": false, "log": [], "status_code": 200}
- `warnings_during_context` = []

import 期捕获到的 starlette/httpx 弃用警告原文：

```json
[
  {
    "category": "StarletteDeprecationWarning",
    "filename": "/Users/chtholly/Developer/WorkSpace/MAA-API/.venv/lib/python3.13/site-packages/fastapi/testclient.py",
    "lineno": 1,
    "message": "Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead."
  },
  {
    "category": "DeprecationWarning",
    "filename": "/Users/chtholly/Developer/WorkSpace/MAA-API/.venv/lib/python3.13/site-packages/starlette/testclient.py",
    "lineno": 53,
    "message": "The anyio.abc.BlockingPortal alias is deprecated, use anyio.from_thread.BlockingPortal instead."
  }
]
```

使用期捕获：

```json
[]
```


## 4. required_checks 一览

| check | 结果 |
|---|---|
| `union_tag_invalid_type_confirmed` | ✅ |
| `union_tag_not_found_distinct_from_invalid` | ✅ |
| `app_error_propagates_unwrapped` | ✅ |
| `value_error_wrapped_with_ctx_error` | ✅ |
| `ctx_error_not_json_serializable_recorded` | ✅ |
| `fastapi_422_for_model_level_errors` | ✅ |
| `handler_maps_error_types_to_400_422` | ✅ |
| `malformed_json_default_is_422_json_invalid` | ✅ |
| `pipeline_union_loc_records_index` | ✅ |
| `legacy_extra_ignore_control` | ✅ |
| `forbid_config_inherited_by_subclass` | ✅ |
| `raise_server_exceptions_default_reraises` | ✅ |
| `schema_x_extensions_preserved` | ✅ |
| `schema_object_shape_ok` | ✅ |
| `schema_nullable_and_numeric_enum_ok` | ✅ |
| `schema_defs_and_ref_shape_ok` | ✅ |
| `alias_schema_and_dump_keys_ok` | ✅ |
| `type_branch_introspection_ok` | ✅ |
| `model_fields_set_three_states` | ✅ |
| `docs_maafield_extra_collision_confirmed` | ✅ |
| `redirect_slashes_disabled` | ✅ |
| `tags_metadata_order_preserved` | ✅ |
| `operation_id_from_route_name_and_tag` | ✅ |
| `route_object_type_recorded` | ✅ |
| `http_exception_headers_passthrough` | ✅ |
| `exception_handler_500_body_ok` | ✅ |
| `testclient_lifespan_ok` | ✅ |
| `starlette_httpx_deprecation_recorded` | ✅ |
| `versions_recorded` | ✅ |

## 6. 复现方式

```bash
.venv/bin/python scripts/probe_api_skeleton.py
```

脚本内定义了全部探针模型与探针 app，不 import `maa_api`，不访问网络/内核/真实设备/仓库库文件；重跑会覆盖 `tests/fixtures/api_probe_result.json` 与 `tests/fixtures/api_probe_findings.md`。
