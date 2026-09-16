# M2-01 实测记录：SQLModel + Alembic 在本机 SQLite 上的 DDL 能力边界

- 生成时间：2026-09-16T12:33:27.159634+00:00
- 环境：Python 3.13.3 / SQLAlchemy 2.0.54 /
  SQLModel 0.0.42 / Alembic 1.20.0 /
  SQLite 3.49.1（macOS-15.4.1-arm64-arm-64bit-Mach-O）
- 复现命令：`.venv/bin/python scripts/probe_sqlmodel_alembic.py`（`--help` 不做实验；所有实验都在 `tempfile.mkdtemp()`
  内完成，仓库里不会留下 `alembic.ini` / `migrations/` / `*.db`）

> 本文件全部结论来自实测；与 docs/04 的写法冲突处以本文件为准，docs 的推断在下面逐条标注。
>
> **M2-14 修正（2026-09-16）**：§2 与 §6.5 原先记录的 `auto_vacuum`「唯一生效放置」只写了
> `conn.exec_driver_sql("PRAGMA auto_vacuum=INCREMENTAL")`、漏了紧跟的 `conn.commit()`。
> 该缺陷由 **M2-04** 实测发现（照抄后 `alembic_version` 行为空、第二次 `upgrade head` 报
> `table already exists`，见 `.refactor/DEFECTS.md` 的 M2-01 条目）；M2-14 已按实测补全
> 说明，并给探针补上「`alembic_version` 行 == 预期版本」断言与「不 commit」对照场景，
> 使这类错误能被探针本身挡住。

## 结论速览

| 问题 | 实测结论 |
|---|---|
| 命名约定 | 表定义**之前**设置才生效（`naming_convention_applied = 是`）；表定义之后才设置时，约定名**不跟随** |
| `AUTOINCREMENT` | DDL 里确实落下了关键字；写法：`id: int \| None = Field(default=None, primary_key=True)` + `__table_args__ = {'sqlite_autoincrement': True}` |
| 部分唯一索引 | `sqlite_where` 的 `WHERE` 子句进入了 DDL |
| `auto_vacuum` | 生效的放置方式：`env_py_before_begin_transaction`，且 pragma 之后**必须 `conn.commit()`**（漏了会让版本行被回滚，见第 2 节） |
| autogenerate | 部分索引谓词被保留；产物直接 `upgrade` **跑不通**（`import sqlmodel` 缺失，见第 3 节） |
| batch downgrade | `batch_downgrade_ok = 是`；注意 batch 重建会丢 `AUTOINCREMENT`（见第 4 节） |
| greenlet | 已安装（`async_engine_usable = 是`） |

## 1. AUTOINCREMENT 的正确写法

实测 DDL（`probe_child`）：

```sql
CREATE TABLE probe_child (
	id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT, 
	parent_id VARCHAR(36) NOT NULL, 
	auditor_id VARCHAR(36), 
	label VARCHAR(16) NOT NULL, 
	CONSTRAINT fk_probe_child_parent_id_probe_parent FOREIGN KEY(parent_id) REFERENCES probe_parent (id) ON DELETE CASCADE, 
	CONSTRAINT fk_probe_child_auditor_id_probe_parent FOREIGN KEY(auditor_id) REFERENCES probe_parent (id) ON DELETE SET NULL
)
```

- 让 DDL 落下 `AUTOINCREMENT` 的写法是：`id: int | None = Field(default=None, primary_key=True)`
  **加上** `__table_args__ = {'sqlite_autoincrement': True}`。只写 `primary_key=True`
  时 DDL 是 `id INTEGER NOT NULL PRIMARY KEY`，**没有** `AUTOINCREMENT`
  （对照实验 `autoincrement_without_flag_control.emitted = 否`，
  对照 DDL：`CREATE TABLE probe_no_autoinc (
	id INTEGER NOT NULL, 
	label VARCHAR(16) NOT NULL, 
	PRIMARY KEY (id)
)`）。
- `create_all` 之后库里会多一张内部表 `sqlite_sequence`（实测：`sqlite_sequence_present = 是`），
  这正是 SQLite 用来保证 id 永不回退的序列表，可以作为「AUTOINCREMENT 真的生效」的旁证。
- 结论：docs/04 §3.1 的硬约束成立，M2-03 给 `log_entry` / `agent_message` / `agent_audit`
  三张表都必须写 `__table_args__ = {'sqlite_autoincrement': True}`；只写自增主键是不够的。

## 2. `auto_vacuum`：哪种放置生效，哪种静默失效

四种放置方式各自在**全新临时库**里真跑 `alembic upgrade head`，升级完成后用**新连接**读
`PRAGMA auto_vacuum`（0 = NONE，1 = FULL，2 = INCREMENTAL），并核对 `alembic_version`
表里是否有且仅有 `0001` 这一行 —— 只查「表存在」挡不住版本行被回滚
（M2-14 补，起因见下）：

| 放置方式 | `PRAGMA auto_vacuum` 实测值 | `alembic_version` 实测行 | 结论 |
|---|---|---|---|
| `migration_first_statement` | 0 | `0001` | ❌ 未生效（0 = NONE，静默失效） |
| `env_py_before_begin_transaction` | 2 | `0001` | ✅ 生效 |
| `migration_autocommit_block` | 0 | `0001` | ❌ 未生效（0 = NONE，静默失效） |
| `env_py_before_begin_transaction_no_commit` | 2 | **空（0 行）** | ⚠️ **不可用**：pragma 拿到 2，但 `alembic_version` 行被回滚 |

- 生效写法：`env_py_before_begin_transaction`。
  对应的代码形态是在 `env.py` 的 `run_migrations_online()` 里、`context.begin_transaction()` **之前**，于 connection 上执行 `conn.exec_driver_sql("PRAGMA auto_vacuum=INCREMENTAL")`；此时库还是空的（`alembic_version` 尚未创建），pragma 被写进库头立即生效。
- **`PRAGMA` 之后必须紧跟一次 `conn.commit()`（M2-04 实测发现，M2-14 补入本文件）**。
  正确形态一共两行，缺第二行就会出上面那条坑：

  ```python
  conn.exec_driver_sql("PRAGMA auto_vacuum=INCREMENTAL")
  conn.commit()  # ⚠️ 不能省：见下一条
  ```

  本场景（`env_py_before_begin_transaction`）实测 `PRAGMA auto_vacuum = 2`、
  `alembic_version` 行 = `0001`；
  紧接着的第二次 `upgrade head` 实测为无操作、无报错（幂等）。
- **不 `commit()` 的具体现象（对照场景 `env_py_before_begin_transaction_no_commit`，M2-14 实测复现 M2-04 的发现）**：
  只执行 pragma、不提交时，实测 `PRAGMA auto_vacuum = 2`、
  `alembic_version` 行 = **空（0 行）**。原因是
  `exec_driver_sql()` 会 autobegin 一个 SQLAlchemy 事务；Alembic 的 `MigrationContext`
  只要发现连接上已有事务就把它当「外部事务」，于是 `begin_transaction()` 退化成 no-op、
  迁移结束也不提交：DDL 因为 pysqlite 不把 DDL 包进事务而留在库里（**表建好了**），
  但 `alembic_version` 的 INSERT 随连接关闭一起回滚（**版本行没了**）。
  第二次 `upgrade head` 实测：`OperationalError: (sqlite3.OperationalError) table probe_min already exists [SQL: CREATE TABLE probe_min ( id INTEGER NOT NULL, name VARCHAR(16) NOT NULL, CONS…`
  —— 版本行为空使 Alembic 从头重放 0001，撞上 `table ... already exists`。
  2026-09-16 的 M2-14 探针跑出这条对照，正是 M2-04 写 `env.py` 初版时踩到的现象。
- **docs/04 §2 原本的写法（0001 `upgrade()` 首行）实测无效**：值仍是 0，而且没有任何报错 ——
  典型的静默失效。原因是 Alembic 在跑第一个迁移之前已经建好了 `alembic_version` 表，
  库不再是空库，SQLite 只在「库为空」时立即接受 `auto_vacuum` 变更，否则要整库 `VACUUM` 才生效。
- `op.get_context().autocommit_block()` 同样无效（值 0）：它只解决「事务里不能改」的问题，
  解决不了「库非空」，`alembic_version` 此时已经存在。
- 四种都没拿到 2 时才需要兜底实验；本次兜底未触发，预留的兜底候选是「pragma + `VACUUM`」与「迁移前用裸 `sqlite3` 连接在空库上设置」。
- **给 M2-04 的落点**：把 `PRAGMA auto_vacuum=INCREMENTAL` 从初始迁移挪到
  `maa_api/db/migrations/env.py` 的 `run_migrations_online()` 开头（`context.configure` 之前），
  **紧跟一次 `conn.commit()`**，并留一条注释说明「写在迁移里是静默无效的、不 commit 会丢版本行」；
  `connect` 事件里同样不能写（docs/04 §2 的判断正确，只是落点选错了）。
  M2-04 的 `env.py` 现状即按此实现（pragma → `conn.commit()` → `context.configure`），
  M2-14 的探针实测其成品（`env_py_before_begin_transaction`）版本行为 `0001`。

## 3. autogenerate 漏了什么、需要人工补什么

用 docs/04 §8.2 的配置（`render_as_batch=True, compare_type=True, compare_server_default=True`）
对同一份模型跑了三次 `alembic revision --autogenerate`：空库（看漏报）、
空库 + 修好的 `script.py.mako`（看修复候选）、`create_all` 已建好的库（看误报）。
每次都把产物真跑一次 `upgrade head`。

`autogenerate_ran = 是`；
`autogenerate_detected_partial_index = 是`
（定义：生成物里既有 `sqlite_where` 又有谓词 `status = 'running'`）。

逐条 diff：

1. 空库 autogenerate 的 upgrade() 操作序列：create_table, batch_alter_table, create_index, create_index, create_table
2. create_table 覆盖：probe_parent, probe_child；create_index 覆盖：ix_probe_parent_status_created_at, uq_probe_parent_running_status
3. 部分唯一索引 uq_probe_parent_running_status：生成物**带** sqlite_where 谓词 —— docs/04 §8.2 说 autogenerate 检测不到部分索引，本次在 Alembic 1.20.0 上**未能复现**该结论（但仍需人工过一遍，见下）
4. probe_child 的 create_table：保留了 sqlite_autoincrement=True，AUTOINCREMENT 不丢
5. JSON 列的 server_default：生成物带 server_default=sa.text("'{}'")
6. 命名约定：生成物沿用了约定的索引/约束名（含 ix_probe_parent_status_created_at / fk_probe_child_parent_id_probe_parent / pk_probe_parent）
7. 外键级联：生成物带 ondelete='CASCADE'（SET NULL 同理需人工核对）
8. **SQLModel 类型 import 缺失**：字符串列被渲染成 `sqlmodel.sql.sqltypes.AutoString(length=...)`，而生成文件只有 `import sqlalchemy as sa`，没有 `import sqlmodel`（生成物含 import sqlmodel = False）
9. 把 autogenerate 产物直接 `upgrade head`：**失败**，原始报错 `NameError: name 'sqlmodel' is not defined` —— 产物开箱即用是不可行的，必须补 sqlmodel import 或改写类型渲染
10. 修复验证：把 `import sqlmodel` 写进 `script.py.mako` 后重新 autogenerate，产物可以直接 `upgrade head`，且落库 DDL 里 AUTOINCREMENT = 在、部分索引 WHERE 谓词 = 在
11. 已建库 autogenerate：生成物为空操作（无 server_default / JSON / 命名 / 类型噪声）

- 空库那次生成的操作序列：`create_table, batch_alter_table, create_index, create_index, create_table`
- 已建库那次的噪声操作：`（无，说明没有误报）`
- **最重要的一条：`autogenerate_ran` 不等于「产物能用」。** 实测
  `autogenerate_generated_migration_applies = 否`，
  原始报错 `NameError: name 'sqlmodel' is not defined`：SQLModel 的 `Field(max_length=...)`
  在元数据里是 `sqlmodel.sql.sqltypes.AutoString`，autogenerate 按 `模块.类` 渲染，
  但**不会自动补 `import sqlmodel`**，于是生成文件一执行就 `NameError`。
  修复候选已验证：把 `import sqlmodel` 写进 `script.py.mako` 后重新生成，
  `autogenerate_mako_fix_applies = 是`
  （若失败，报错：`（无）`），
  且落库 DDL 里 `AUTOINCREMENT = 是`、
  部分索引 `WHERE` 谓词 = 是。
- docs/04 §8.2 说「autogenerate 检测不到 `sqlite_where` 的部分索引」：本次在
  Alembic 1.20.0 + SQLModel 0.0.42 上**未能复现**
  （谓词被完整生成，连 `sqlite_autoincrement=True`、`server_default`、`op.f()` 约定名都保住了）。
  「索引重命名检测不到」这一条本卡没有单独设对照，仍按 docs 的人工 review 纪律执行。
- 结论：**生成物仍然必须人工过一遍**，至少检查
  ①`import sqlmodel` / `AutoString` 渲染（本次唯一实测会炸的项）、
  ②部分唯一索引的 `sqlite_where` 谓词、③`sqlite_autoincrement=True`、
  ④JSON 列的 `server_default`、⑤外键 `ondelete` 与约定名。
  M2-04 应把 `import sqlmodel` 直接写进 `script.py.mako`，并把上面五项列进 review 清单。

## 4. batch + downgrade

第二个迁移用 `op.batch_alter_table("probe_child", recreate="always")` 同时加一列
（`note VARCHAR(32) NULL`）并把 `label` 从 `VARCHAR(16)` 改成 `VARCHAR(24)`，
流程是 `upgrade 0001` → 快照 → `upgrade head` → `downgrade -1` → 快照比对。

- `batch_upgrade_ok = 是`，
  `batch_change_observed = 是`：
  upgrade 后列集合 = `['id', 'parent_id', 'auditor_id', 'label', 'note']`，`label` 类型 = `VARCHAR(24)`。
- `batch_downgrade_ok = 是`（列/外键/显式索引结构指纹与上一版一致）；
  downgrade 后列集合 = `['id', 'parent_id', 'auditor_id', 'label']`。
- 数据不丢：`batch_upgrade_preserved_rows = 是`（重建前后都是 1 行）。
- 命名外键在重建后仍在：`batch_upgrade_preserved_fk_names = 是`
  —— 这正是 docs/04 §8.1 要求「约束先命名」的原因，匿名约束在 batch 重建时会丢名字。
- **本卡最重要的意外发现：纯反射的 batch 重建会静默丢掉 `AUTOINCREMENT`。**
  实测 `batch_upgrade_preserved_autoincrement = 否`，
  `batch_downgrade_preserved_autoincrement = 否`；
  upgrade 前 DDL：`CREATE TABLE probe_child ( id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT, parent_id VARCHAR(36) NOT NULL, auditor_id VARCHAR(36), label VARCHAR(16) NOT NULL, CONSTRAINT fk_probe_child_parent_id_probe_parent FOREIGN KEY(parent_id) REFERENCES probe_parent (id) ON DELETE CASCADE, CONSTRAINT fk_probe_child_auditor_id_probe_parent FOREIGN KEY(auditor_id) REFERENCES probe_parent (id) ON DELETE SET NULL )`；
  batch 重建后 DDL：`CREATE TABLE "probe_child" ( id INTEGER NOT NULL, parent_id VARCHAR(36) NOT NULL, auditor_id VARCHAR(36), label VARCHAR(24) NOT NULL, note VARCHAR(32), PRIMARY KEY (id), CONSTRAINT fk_probe_child_parent_id_probe_parent FOREIGN KEY(parent_id) REFERENCES probe_parent (id) ON DELETE CASCADE, CONSTRAINT fk_probe_child_auditor_id_probe_parent FOREIGN KEY(auditor_id) REFERENCES probe_parent (id) ON DELETE SET NULL )`；
  downgrade 后 DDL：`CREATE TABLE "probe_child" ( id INTEGER NOT NULL, parent_id VARCHAR(36) NOT NULL, auditor_id VARCHAR(36), label VARCHAR(16) NOT NULL, PRIMARY KEY (id), CONSTRAINT fk_probe_child_parent_id_probe_parent FOREIGN KEY(parent_id) REFERENCES probe_parent (id) ON DELETE CASCADE, CONSTRAINT fk_probe_child_auditor_id_probe_parent FOREIGN KEY(auditor_id) REFERENCES probe_parent (id) ON DELETE SET NULL )`。
  原因：`batch_alter_table` 重建时靠 SQLAlchemy 反射拿旧表结构，而 SQLite 反射**不还原**
  `sqlite_autoincrement` 表选项，于是 `id INTEGER PRIMARY KEY AUTOINCREMENT` 变成
  `id INTEGER PRIMARY KEY`，id 回退保护就此消失。
- 兜底做法实测（给 `batch_alter_table` 传 `copy_from`，不让它反射）：
  upgrade 侧传模型自己的 `SQLModel.metadata.tables["probe_child"]`，
  `batch_copy_from_upgrade_ok = 是`、
  `batch_copy_from_upgrade_preserved_autoincrement = 是`；
  downgrade 侧必须传「0002 之后的库结构」（模型表 `to_metadata()` 再补上 `note` 列），
  `batch_copy_from_downgrade_ok = 是`、
  `batch_copy_from_downgrade_preserved_autoincrement = 是`。
  结论：**`copy_from` 的表结构必须与「该方向的迁移前结构」一致，只补一个方向不够**；
  涉及 `log_entry` / `agent_message` / `agent_audit` 这类带 `AUTOINCREMENT` 的表时，
  upgrade 与 downgrade 都要显式给 `copy_from`，并在迁移后用 `sqlite_master` 复查 DDL。
- 给 M2-04 的纪律：凡是要 rebuild 表的迁移（改列类型 / 加删约束 / 删列），
  ①反射式重建会静默丢 `AUTOINCREMENT`，所以 `copy_from` 必须补；
  ②`copy_from` 的表结构要与该方向的迁移前结构一致（升级用模型 Table，降级用迁移后的结构）；
  ③迁移落库后检查 `sqlite_master` 里那张表的 DDL 是否还有 `AUTOINCREMENT`。
  纯加列的迁移（`ALTER TABLE ADD COLUMN`）不重建表，不受影响。

## 5. greenlet 缺失的原始报错与影响面

- `greenlet_available = True`（已安装），异步引擎可用性 = 是，`select 1` 返回值 = 1。
- 本机当前**已能**创建异步引擎，M2-02 仍需把 greenlet 写进显式依赖（aiosqlite/SQLAlchemy async 的硬要求），不要依赖间接传递。

## 6. 给 M2-03 / M2-04 的具体写法建议

1. **models.py 顶部顺序**：先 `SQLModel.metadata.naming_convention = {...}`，再定义任何
   `table=True` 的模型。实测表定义之后才设置时，无名 `Index` / `UniqueConstraint` / 外键
   **不会**跟随约定名（对照缺失项：`['ix_probe_late_child_parent_id_status', 'uq_probe_late_child_parent_id_status', 'fk_probe_late_child_parent_id_probe_late_parent', 'pk_probe_late_parent']`）。
2. **三张高频追加表**写 `__table_args__ = {'sqlite_autoincrement': True}`，
   否则 DDL 里没有 `AUTOINCREMENT`，日志表按天清理后会复用已删除的 id，WebSocket 断线续传游标失效。
3. **部分唯一索引**（`uq_update_record_running_target`、`uq_probe_parent_running_status` 这类）
   建议照 docs/04 §6 手写 `Index(name, col, unique=True, sqlite_where=sa.text(...))`：
   本次实测 autogenerate 会保留谓词，
   但「生成物能不能直接跑」还取决于下一条，手写最稳。
4. **`script.py.mako` 必须补 `import sqlmodel`**：否则所有 `Field(max_length=...)` 字符串列会被
   渲染成 `sqlmodel.sql.sqltypes.AutoString(...)`，生成文件一执行就
   `NameError: name 'sqlmodel' is not defined`（本次实测产物
   `upgrade head` 失败：`NameError: name 'sqlmodel' is not defined`）。
   补上之后 `autogenerate_mako_fix_applies = 是`。
   备选方案是在 env.py 里用 `render_item` 把 `AutoString` 渲染成 `sa.String(length=...)`。
5. **env.py**：`render_as_batch=True, compare_type=True, compare_server_default=True` 照抄，
   并在 `run_migrations_online()` 里 `context.begin_transaction()` 之前加
   `conn.exec_driver_sql("PRAGMA auto_vacuum=INCREMENTAL")`，**紧跟一次 `conn.commit()`**
   （M2-14 修正：M2-01 原记录漏了这一行，M2-04 照抄后 `alembic_version` 行为空、第二次
   `upgrade head` 报 `table already exists`；实测见第 2 节）；
   不要写进初始迁移的 `upgrade()`（实测静默无效）。
6. **迁移后的断言不能只看表**：每次 `upgrade head` 之后都要核对 `alembic_version` 有且仅有
   预期 revision 这一行（M2-14 补，探针的 `auto_vacuum_version_row_ok` /
   `auto_vacuum_no_commit_control_reproduced` 两条门禁即为此设）。表建好而版本行为空时，
   下一次 `upgrade head` 会从头重放并撞 `table already exists`。
7. **迁移 review 清单**：`import sqlmodel`、`sqlite_where` 谓词、`sqlite_autoincrement=True`、
   JSON `server_default`、`ondelete` 级联、约定名；涉及 rebuild 的迁移额外检查 `copy_from`
   与迁移后的 `AUTOINCREMENT`/`foreign_keys` 是否还在。
8. **异步层（M2-02）**：greenlet 必须显式加依赖；在装上之前的 async 代码无法运行。
