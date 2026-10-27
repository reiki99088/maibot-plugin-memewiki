# 验证记录

基线：MaiBot 1.3.15、maibot-plugin-sdk 2.16.0，源码快照在 `E:\Agents\Work\memewiki-maibot-port\upstream\`；源插件 astrbot_plugin_memewiki v1.5.2（commit 386dd63）。

## 2026-10-27 · 0.1.0 开发版

环境：Windows 11，Python 3.12.10，项目内 `.venv`（aiohttp 3.14.5、Pillow 13.0.0、pydantic 2.14.1、pytest 9.1.1），SDK 从本地快照安装。

`python -m pytest`：**83 passed，0 skipped**。

### 已验证（有测试或一手证据）

| 项目 | 怎么验证的 |
|---|---|
| 内置库与上游一致 | `data/memes.db` 与上游 sha256 相同（05d902ff…），6668 条，构建于 2026-10-26 19:07:22 |
| 查词规则 | 精确命中、句内取最长标题、前缀不算、单字标题只认精确；同步库优先于内置库；无 db 时回退 index.json（`test_store.py`） |
| 查询后不留句柄 | 查完立刻 `replace` + `unlink` 库文件不报 WinError 32（`test_no_open_handle_after_query`） |
| 问梗闸门 | 上游 README 的 8 条示例消息，结果与预期一致（`test_gate.py`） |
| 查询优先级与缓存 | 群梗 → 自定义黑话 → 内置库 → 在线；加黑话后清空全部缓存，含该词的旧句子能查到新结果（`test_service.py`） |
| 预查 | 本地命中、不像梗不联网、离线、在线超时进入 10 分钟未命中缓存、在线命中后详情落盘、严格模式拒绝无关结果；在线部分用打桩，测试一律禁止真实联网（`conftest.no_network`） |
| 黑话存储 | 校验长度、保存前写 `.bak`、tmp 后整体替换、坏文件当空表（`test_terms.py`） |
| 卡片 | 用法和出处都为空的词条也能渲染；行首不出现标点；无中文字体时 `/梗` 改发文字（`test_card.py`、`test_plugin.py`）。另外人工看过「尼罗河二圣」「命苦笑」两张卡片 |
| 打包脚本 | 抓取目录 → SQLite → 插件可读，旧库被整体替换，不留 `.building`（`test_pack_data.py`）。测试数据的 HTML 格式按 cnmeme.wiki 实际返回的格式（`</h2>` 后有换行）编写，这一点是用插件 UA 实际请求了一条公开词条确认的 |
| 清单 | 用宿主 `ManifestValidator` 原代码校验通过；宿主版本低于 1.3.13 时拒绝；SDK 版本越界时宿主只告警放行；依赖写 `version` 而不是 `version_spec` 会被拒（`test_manifest_host.py`） |
| WebUI 声明 | 用宿主 `webui_schema.load_webui_extension` 原代码校验通过，页面引用的 API 都已声明（`test_plugin.py`） |
| Runner 加载 | 用宿主 `PluginLoader` 原代码从模拟的 `plugins/` 目录加载：清单 → 依赖 → 合成包导入（相对导入）→ `create_plugin` → 生命周期契约检查都通过，随后 `on_load`、调用工具、`on_unload` 正常；去掉 `on_config_update` 时宿主拒绝加载（`test_host_loader.py`） |
| Hook 注入数据 | `inject_planner` 返回的 items 用宿主 `deserialize_context_item_snapshot` + `validate_context_items(REQUEST_CONTEXT)` 原代码校验通过；返回的 kwargs 保留了 `item_schema_version` 等其它参数；作为对照，重复 item_id 或缺少版本号会被校验拒绝 |
| 命令与 API | `/梗`、`/黑话` 的增、列、删、权限（本机控制台 / `admin_users`，平台名不区分大小写）、参数校验；WebUI 三个 API（`test_plugin.py`，假上下文） |

### 未验证（需要运行中的 MaiBot）

- 宿主进程里的完整链路：Host 与 Runner 之间的 RPC、组件注册、配置文件生成、WebUI 配置页和侧栏页的实际显示。
- `/梗` 在真实聊天平台上的图片发送（`send.image` 的 base64 是否被适配器接受）。
- 两个 Hook 在真实消息流里的触发时机，以及 `chat.receive.after_process` 载荷里字段的实际取值（`session_id`、`is_command`、`processed_plain_text`）。目前只按宿主源码确认了 Hook 名称和参数名。
- 模型拿到注入资料后的实际回复质量。
- 每日同步在宿主环境中的子进程运行（`sys.executable` 是否为 Runner 的解释器）。
- 宿主依赖流水线（uv）自动安装 aiohttp / Pillow 的过程。
- Docker 镜像里的字体情况。

### 开发中发现、已修正的问题

- **Hook 返回值会整体替换 kwargs**：宿主 `hook_dispatcher` 用 `modified_kwargs` 直接替换原 kwargs，不做合并。最初只返回了 `items`，导致 `item_schema_version` 丢失，宿主会以“版本不受支持”为由忽略注入。现改为原样带回其它参数，只替换 `items`，并加了测试。
- 交接文档里有几处与宿主源码不符，已按源码修正：清单依赖字段是 `version_spec`；Command 返回 `(成功, 说明, 是否拦截)` 三元组；1.3.15 不派发 `ON_MESSAGE` 事件（`bot.py` 中已注释），预查改用 `chat.receive.after_process` Hook；WebUI 声明是 schema v2；`plugin.py` 必须用相对导入。
