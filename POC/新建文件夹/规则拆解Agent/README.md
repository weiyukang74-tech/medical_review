# 规则拆解 Agent（POC）

本模块只完成一件事：读取负面清单 Excel，调用一个通过 OpenAI 兼容接口提供的 27B 或 32B 指令模型，将每条规则拆成待判断事实、AND/OR/NOT 逻辑关系、判定方向、时间要求和待澄清项。

## 目录

```text
POC/
├─ 负面清单/
│  └─ 负面清单.xlsx
├─ 规则拆解Agent/
│  ├─ rule_agent/
│  ├─ schemas/
│  ├─ tests/
│  ├─ .env.example
│  └─ requirements.txt
└─ 规则拆解结果/             # 运行后生成
   └─ 规则拆解结果.json       # 所有规则统一追加到这一个文件
```

## 模型要求

- 参数规模只能配置为 `27B` 或 `32B`。
- 服务需要提供 OpenAI 兼容的 `/v1/chat/completions` 接口。
- 推荐使用 `MODEL_RESPONSE_FORMAT=json_object`。程序只要求模型返回可解析的 JSON 对象，具体字段、拆解逻辑和输出要求全部由系统提示词定义。
- 模型名称由部署平台决定。当前选用百炼模型 `qwen3.6-27b`，满足 27B/32B 的规模约束。不要把 API Key 写进代码或提交到仓库。

## 安装与配置

```powershell
cd D:\AI\AI_S2\POC\规则拆解Agent
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

当前 POC 采用阿里云百炼华北2（北京）的 `qwen3.6-27b`。复制配置后，只需填写 API Key：

```text
MODEL_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
MODEL_API_KEY=在百炼控制台创建的API Key
MODEL_NAME=qwen3.6-27b
MODEL_PARAM_SCALE=27B
MODEL_RESPONSE_FORMAT=json_object
MODEL_ENABLE_THINKING=false
```

`.env` 中的 `MODEL_ENABLE_THINKING` 是默认值；PowerShell 命令行可以为单次运行覆盖。百炼接口中的模型 ID 必须填写 `qwen3.6-27b`。

## 运行

当前 `负面清单.xlsx` 的三列表头为“住院门诊号、就诊号、违规内容”。程序按数据行顺序自动生成 `RULE-001`、`RULE-002`……，不会把 Excel 的字体或底色传给模型。

先检查 Excel，不请求模型：

```powershell
python -m rule_agent --dry-run
```

调用模型并生成规则拆解结果：

```powershell
python -m rule_agent --env-file .env
```

程序固定使用追加模式：已有 `rule_id` 会跳过，只调用模型处理汇总文件中尚不存在的规则，不提供覆盖模式。

```powershell
python -m rule_agent --env-file .env --write-mode append
```

## 本地可视化工作台

启动工作台：

```powershell
python -m rule_agent.web_app
```

然后打开 `http://127.0.0.1:8765`。页面可以查看全部规则及现有拆解逻辑、编辑并校验单条规则 JSON、编辑并保存系统提示词、切换思考模式，以及选择追加或全部更新。服务只监听本机地址，页面不显示 API Key。

### Cloudflare 静态审阅版

发布前生成一次静态数据快照：

```powershell
python -m rule_agent.export_web_snapshot
```

然后将 `web_ui\dist` 目录完整发布到 Cloudflare Pages。快照包含规则原文、已有拆解结果和系统提示词，不包含 `.env` 或 API Key。Cloudflare 页面无法连接本地后端时会自动进入“云端审阅模式”：可以查看、编辑并下载单条 JSON，但不能在云端运行 Agent、保存提示词或直接覆盖本地文件。

本地规则或拆解结果更新后，需要重新执行快照命令并重新发布，云端才能显示新内容。

单次运行开启思考模式：

```powershell
python -m rule_agent --env-file .env --thinking
```

单次运行关闭思考模式：

```powershell
python -m rule_agent --env-file .env --no-thinking
```

不写开关时使用 `.env` 中的 `MODEL_ENABLE_THINKING`。开启思考模式后，终端显示基于最终 JSON 生成的拆解摘要；模型返回的原始 `reasoning_content` 不写入结果文件。

也可以显式指定输入输出：

```powershell
python -m rule_agent --input "..\负面清单\负面清单.xlsx" --output-dir "..\规则拆解结果"
```

所有模型结果统一保存在 `规则拆解结果.json` 数组中。每项包含 `_comment`、`rule_id` 和模型原始 `result`，其中 `_comment` 使用 `----------------- RULE-001 -----------------` 作为视觉分隔。程序只追加尚不存在的 `rule_id`，不会更新或覆盖已有条目。文件不存在或内容为空时按全新任务处理，不会自动导入任何旧版单条结果或备份。

## 测试

测试使用假模型，不调用外部接口：

```powershell
python -m unittest discover -s tests -v
```

## 当前边界

- 只处理 Excel 中状态为“已发布”的规则。
- 当前阶段不生成证据标签，不包含规则画像编号、版本、项目或发布状态。
- Agent 只能依据单条规则原文拆解，不能自行补充临床阈值或医保口径。
- 真实模型接入前必须由接口提供方确认模型 ID、Base URL、鉴权方式及结构化输出兼容性。
