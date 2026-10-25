# QQDealWatch

监听指定 QQ 群的优惠消息，按自定义商品清单和可选价格上限筛选，再通过 pushplus 通知手机。支持 SnowLuma 或 NapCatQQ 的 OneBot v11 正向 WebSocket 接口、OpenAI 兼容的 AI 接口，以及本机 RapidOCR 图片文字识别。

## 功能

- 只处理配置的群，按商品别名初筛，再由 AI 判断型号、规格、价格与附加条件。
- 价格上限为严格小于；不设上限时，只要商品匹配即可提醒。
- 同一发送者两分钟内的消息可合并理解，例如先发商品，再补价格。
- 图片中的文字由本机 OCR 提取。QQ 图片的 HTTP 链接会升级为 HTTPS，仅下载可信 QQ 图片域名，单图上限 6 MB。
- SQLite 记录已处理消息、去重和失败重试；推送支持 pushplus 微信服务号或 App 渠道。
- 可选的 pushplus 语音电话默认关闭，启用后受每日次数、冷却时间和时段限制。
- 不向 QQ 群发送消息。

## 快速开始（Windows）

1. 安装 Python 3.11+，然后在项目目录运行：

   ```powershell
   python -m pip install -r requirements.txt
   Copy-Item config.example.json config.json
   Copy-Item secrets.example.env secrets.env
   ```

2. 安装并登录 [SnowLuma](https://github.com/SnowLuma/SnowLuma) 或 [NapCatQQ](https://github.com/NapNeko/NapCatQQ)，启用 OneBot v11 **正向 WebSocket 服务端**，建议仅监听本机。示例配置使用 `ws://127.0.0.1:3001/`。如果接口设置了访问 token，填入 `secrets.env` 的 `ONEBOT_ACCESS_TOKEN`。

3. 修改 `config.json`：填入你的群号、商品名称、别名、规格要求和可选 `max_price`。示例文件中有两条演示规则。`max_price: null` 表示不限制价格。首次运行保持 `dry_run: true`，此时只记录模拟提醒，不会发送通知。

4. 准备 OpenAI 兼容的聊天补全接口。默认地址为 `http://127.0.0.1:8080/v1/chat/completions`，默认模型为 `glm-5.3-flash`。按需在 `secrets.env` 中设置 `AI_API_URL`、`AI_MODEL`、`AI_API_KEY`。也可通过 `AI_VISION_MODEL` 与 `AI_VISION_API_URL` 改用支持图片输入的视觉模型；未设置时使用本机 OCR。

5. 检查连接并启动：

   ```powershell
   python -m qqym.app --check
   python -m qqym.app --config config.json
   ```

6. 确认演练结果正确后，取得 [pushplus](https://www.pushplus.plus/) 的个人 token，填入 `secrets.env` 的 `PUSHPLUS_TOKEN`。选择 `notify_channel: "wechat"` 时，须关注并绑定 pushplus 微信服务号；也可选 `"app"`。将 `dry_run` 改为 `false` 并重启监听器。

电脑需保持登录且不休眠。QQ 接入程序、AI 服务和监听器都要持续运行。SnowLuma、NapCatQQ 及 AI 服务均需自行安装，本仓库不包含它们的程序或凭据。

## 自己管理关注商品

在项目目录双击 `manage-items.cmd`，按菜单选择“添加商品”。依次填写商品名称、常见简称、规格或排除条件，以及可选价格上限。价格留空表示不限价；例如填写 `700`，只有明确的到手价**低于 700 元**才提醒。菜单也支持查看、调整价格和删除商品；最后一条也能删除，空清单不会产生商品提醒。

保存后，运行中的监听器约 20 秒内自动载入新规则，无需重启；新规则只处理之后收到的群消息，不会自动扫描历史消息。删除商品后，其尚未发送的提醒也会取消；已送达的通知和历史记录不会撤回。`config.json` 是本机私有文件，管理工具不会改动其中的群号或推送设置。

也可以在命令行使用 `python -m qqym.watchlist list` 和 `python -m qqym.watchlist add`。

## 登录后自动启动

`start.ps1` 可启动放在 `tools/SnowLuma` 的 SnowLuma、检查本机 8080 端口并启动 GLM 代理，然后运行监听器。脚本每 30 秒检查依赖服务，退出后会尝试重新启动。GLM 代理默认查找项目目录的同级 `zcode-api/zcode-proxy.exe`；其他位置可设置环境变量 `QQYM_PROXY_EXE` 为其绝对路径。如果使用 NapCatQQ 或其他 AI 服务，可按自己的环境调整启动脚本。

完成实际通知测试后，可运行：

```powershell
.\install-autostart.ps1
```

脚本会为当前 Windows 用户创建登录启动计划任务 `QQYM Monitor`，日志保存在 `logs/`。计划任务要求用户登录，因为 QQ 接入程序是桌面进程。

## 配置说明

| 字段 | 作用 |
| --- | --- |
| `group_ids` | 要监听的 QQ 群号列表 |
| `rules[].aliases` | 商品关键词，负责低成本初筛 |
| `rules[].requirements` | AI 判断型号、规格和排除条件时使用 |
| `rules[].max_price` | 到手价必须低于该值；`null` 不限价 |
| `unknown_price_notify` | 有价格上限但到手价不明时是否发送普通提醒 |
| `dedupe_hours` | 同类优惠的去重时间 |
| `notify_channel` | `wechat` 或 `app` |
| `voice.enabled` | 是否允许语音电话，默认关闭 |

需要会员、返现、以旧换新等额外条件的价格会标记为不确定。电话提醒还要求证据中明确出现到手价。pushplus 接口返回成功仅代表受理请求，实际送达状态应到 pushplus 历史消息或发送结果中确认。

## 隐私与安全

`config.json`、`secrets.env`、SQLite 数据库、日志和下载的 QQ 接入程序均被 Git 忽略。请勿把真实群号、聊天记录、访问 token 或数据库提交到公开仓库。群消息文本和图片 OCR 结果会发送给你配置的 AI 服务，提醒摘要会发送给 pushplus。WebSocket 和本机 AI 接口建议仅监听 `127.0.0.1`。

## 验证

```powershell
python -m unittest discover -s tests -v
```

本项目基于 MIT 许可证开源。QQ、SnowLuma、NapCatQQ、RapidOCR 和 pushplus 属于各自权利人。
