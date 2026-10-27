# 更新日志

## 0.1.0（未发布）

首个 MaiBot 版本，移植自 [astrbot_plugin_memewiki](https://github.com/in1t1a1/astrbot_plugin_memewiki) v1.5.2。

- 内置 6668 条 cnmeme.wiki 词条（SQLite），本地优先，没收录才查在线词库。
- LLM 工具 `search_meme_wiki`：模型遇到看不懂的梗时自己查。
- `/梗 <词>`：图片卡片（PIL 渲染），发送失败退回文字。
- `/黑话`：自建群内黑话，增删改即时生效；删除限本机控制台或 `lookup.admin_users`。
- WebUI 侧栏页「自定义黑话」：列表、新增、删除。
- 实验性预查注入（`inject.prefetch_mode = "hook"`，默认关闭）：入站消息像梗时先查好，下一次规划请求里临时追加一条资料。
- 可选每日从 cnmeme.wiki 增量同步词库（默认关闭）。

与 AstrBot 版的差异：

- 去掉了 `/网搜` 命令和插件内置的搜索引擎；词库没收录时只提示模型可用 `web_search`（`inject.search_mode`）。
- 去掉了按词条注入的命中缓存；预查结果只活 90 秒、只注入对应会话。
- 不写 MaiBot 自带的黑话（Jargon）表，自定义黑话单独存放在插件数据目录。
