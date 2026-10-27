# 梗百科 · MaiBot 插件

让麦麦认得群里的梗和黑话。先查内置词库（6668 条，毫秒级），没收录再查 [cnmeme.wiki](https://cnmeme.wiki) 的公开接口；也可以让群友自己记黑话。

移植自 [in1t1a1/astrbot_plugin_memewiki](https://github.com/in1t1a1/astrbot_plugin_memewiki) v1.5.2（MIT）。查词逻辑、问梗闸门、内置词库和同步脚本都来自原插件，这里只把它接到 MaiBot 的插件 SDK 上。原插件的许可证见 [LICENSE.upstream](LICENSE.upstream)。

> 状态：0.1.0 开发版。单元测试和「宿主加载器加载插件」测试已通过，**还没有在运行中的 MaiBot 上实测过**，详见 [docs/verification.md](docs/verification.md)。

## 能做什么

| 功能 | 说明 |
|---|---|
| 工具 `search_meme_wiki` | 模型遇到看不懂的梗、缩写、黑话时自己调用。查询顺序：群梗 → 自定义黑话 → 内置词库 → cnmeme.wiki |
| `/梗 <词>` | 发一张词条卡片（来源 / 用法 / 标签）；图片发不出去时退回文字 |
| `/黑话 <名称> <含义>` | 记一条群内黑话，立即生效 |
| `/黑话 列表` | 看已记录的黑话 |
| `/黑话 删除 <名称>` | 删除一条；只有本机控制台或 `lookup.admin_users` 里的账号可以删 |
| WebUI「自定义黑话」页 | 在插件侧栏页里增删黑话 |
| 每日同步（默认关） | 每天定时从 cnmeme.wiki 增量更新词库 |
| 预查注入（实验性，默认关） | 消息像在问梗时先查好，在下一次规划请求里临时附上资料 |

## 安装

1. 把本仓库放进 MaiBot 的 `plugins/` 目录（目录名随意，插件 ID 以 `_manifest.json` 为准）。
2. 依赖是 `aiohttp` 和 `Pillow`，MaiBot 会按清单自动安装；也可以手动 `pip install -r requirements.txt`。
3. 重启 MaiBot 或在 WebUI 里重载插件。

需要 MaiBot 1.3.13 及以上、maibot-plugin-sdk 2.16 及以上。

### 卡片字体

`/梗` 的卡片用 Pillow 绘制，需要系统里有中文字体（Windows 自带微软雅黑；Linux 装 `fonts-noto-cjk` 或文泉驿）。找不到中文字体时自动改发文字，不会出方框字。Docker 镜像里通常没有中文字体，可以把 `lookup.reply_mode` 设成 `text`。

## 配置

在 WebUI 的插件配置页修改，或编辑插件目录下的 `config.toml`。

| 配置项 | 默认 | 说明 |
|---|---|---|
| `lookup.reply_mode` | `image` | `/梗` 回图片卡片还是纯文字 |
| `lookup.online_lookup` | `true` | 本地没收录时是否查 cnmeme.wiki |
| `lookup.online_timeout` | `6.0` | 在线查询超时（秒） |
| `lookup.admin_users` | `[]` | 允许删黑话的账号，格式 `平台:账号`，如 `qq:123456` |
| `inject.prefetch_mode` | `off` | `off` 只靠工具；`hook` 打开预查注入（实验性） |
| `inject.inject_guidance` | `true` | 预查注入时附带接梗规则 |
| `inject.guidance_text` | 空 | 自定义接梗规则，留空用内置 |
| `inject.search_mode` | `model` | 词库没收录时：`model` 提示模型自己用 `web_search` 查；`off` 只提示别硬解释 |
| `inject.search_max_chars` | `10` | 不是明确在问梗时，查询词超过这个长度就不当成梗 |
| `inject.wake_words` | `[]` | 额外的唤醒词；机器人昵称和别名会自动加入 |
| `sync.auto_sync_enable` | `false` | 每天自动同步词库 |
| `sync.auto_sync_hour` | `4` | 同步时刻（0–23 点） |

## 和 MaiBot 自带黑话学习的关系

MaiBot 自己会从聊天里学黑话（Jargon），并在规划时附上它猜测的含义。本插件与它**并存、互不写入**：

- 本插件的价值是现成的 6668 条梗词库加 cnmeme.wiki 在线回退，不重复做黑话学习。
- `/黑话` 记的条目存在插件数据目录的 `user_terms.json` 里，不写进 MaiBot 的 Jargon 表（SDK 目前没有提供写入的能力）。
- 想让模型优先参考哪边，可以在人设或接梗规则里说明。

## 预查注入（实验性）

`inject.prefetch_mode = "hook"` 时：

1. 订阅 `chat.receive.after_process`：消息看起来像在问梗，就先查本地词库，没有再限时 1.5 秒查在线词库。
2. 订阅 `maisaka.planner.before_request`：同一会话 90 秒内的下一次规划请求，在请求末尾临时追加一条资料，不写回聊天历史。

不像在问梗的消息（闲聊、指代「你说的」「自己发的」等）不联网，也不注入。这个模式依赖的宿主 Hook 载荷格式没有文档承诺，宿主升级后可能失效，所以默认关闭；关闭时模型仍可通过 `search_meme_wiki` 工具自己查。

原插件里的 `/网搜` 指令和插件代为搜索的功能没有移植：MaiBot 没有给插件开放网页搜索能力，联网搜索交给模型调用宿主的 `web_search` 工具。

## 数据

- 内置库：`data/memes.db`，6668 条，构建于 2026-10-26，与原插件 v1.5.2 附带的库逐字节相同。
- 运行时数据在插件数据目录：同步后的 `memes.db`（优先于内置库）、`user_terms.json`（及 `.bak` 备份）、可选的 `group_slang.json`（`{"词": "含义"}`，手动放置）。
- 手动同步：`python -X utf8 scripts/sync_memes.py --data-dir <插件数据目录>`。

词条内容来自 cnmeme.wiki 的公开只读 API，版权归 cnmeme.wiki 及原作者，卡片上保留了来源链接。自行更新词库时请遵守站点规则：同步脚本默认每 0.35 秒一次请求，遇到 429 会退避。

## 开发

```bash
python -m venv .venv
```

```bash
.venv/Scripts/python -m pip install -r requirements-dev.txt
```

```bash
.venv/Scripts/python -m pytest
```

部分测试会读取 MaiBot 源码，用宿主自己的代码校验清单、WebUI 声明、Hook 返回的 Context Item 和插件加载过程。源码路径用环境变量 `MAIBOT_SRC` 指定；找不到时这些测试会跳过。

## 许可证

MIT，见 [LICENSE](LICENSE)。原插件版权归 in1t1a1，见 [LICENSE.upstream](LICENSE.upstream)。
