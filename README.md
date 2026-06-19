# NC\-Claw v3\.1 README

NC\-Claw 是一款基于 GTK 4 和 Libadwaita 的 AI 驱动型 Linux 系统管理助手，集成 AI 对话、Markdown 渲染、命令执行与自定义命令管理于一体。

# 核心特性

- **AI 驱动命令生成**：对接阿里云通义千问 API，自动生成符合需求的 Shell 命令

- **Markdown 渲染**：完整支持 Markdown 语法的 AI 响应渲染，包含代码块、列表、引用等

- **自定义命令**：预设/自定义常用系统命令，支持快捷调用和启用/禁用

- **命令执行**：一键执行 AI 生成或自定义的命令，实时查看输出

- **执行上报**：自动将命令执行结果反馈给 AI，辅助后续决策

- **API 页面管理**：支持管理自定义 API 请求（GET/POST/PUT/DELETE/PATCH）

- **现代化 UI**：基于 GTK 4 \+ Adw 的原生 Linux 界面，支持圆角组件和响应式布局

# 安装依赖

NC\-Claw 需要 Python 3 和以下依赖库：

```bash
sudo apt install python3 python3-gi gir1.2-gtk-4.0 gir1.2-adw-1
```

```bash
sudo dnf install python3 pygobject3 gtk4 libadwaita
```

# 快速开始

## 1\. 下载与运行

```bash
chmod +x nc-claw.py
./nc-claw.py
```

## 2\. 配置 API

首次运行会自动创建配置目录 `~/.config/nc-claw/`，需先配置 API 密钥：

1. 打开设置页面（Settings）

2. 填写 API Key（阿里云通义千问 API 密钥）

3. 可选：调整 Model（默认 qwen\-plus）、Temperature 等参数

4. 点击「Save Configuration」保存

## 3\. 使用流程

1. 在聊天输入框中输入系统管理需求（例如："查看 nginx 状态"、"更新系统包"）

2. AI 会返回包含 Shell 命令的 Markdown 响应

3. 右侧面板会提取所有可执行命令，支持单个执行或批量执行

4. 执行结果可一键上报给 AI，获取进一步的分析和建议

# 功能模块

|模块|说明|
|---|---|
|Chat|AI 对话主界面，左侧聊天区 \+ 右侧命令提取面板，支持 Run \& Report 模式|
|Commands|命令运行器，支持直接输入命令执行，内置常用系统命令快捷按钮|
|API Editor|API 页面与自定义命令的统一编辑器，支持增删改查和发送测试请求|
|Settings|系统设置，配置 API 端点、密钥、模型、温度、系统提示词等参数|

# 配置参数说明

|参数名|默认值|说明|
|---|---|---|
|api\_endpoint|通义千问兼容端点|OpenAI 兼容格式的 API 端点地址|
|api\_key|空|阿里云 API 密钥（必填）|
|model|qwen\-plus|使用的模型名称|
|max\_tokens|4096|最大生成令牌数|
|temperature|0\.7|生成随机性（0\-2，值越高越随机）|
|system\_prompt|内置提示|AI 系统提示，定义助手行为|
|confirm\_execution|True|执行命令前是否确认（预留功能）|

# 自定义命令

自定义命令存储在 `~/.config/nc-claw/custom_commands.json`，默认包含：

- `nginx-status`：检查 nginx 服务状态

- `nginx-restart`：重启 nginx 服务

- `sys-update`：更新系统包

## 添加自定义命令

1. 进入 API Editor 页面

2. 切换到 Commands 模式

3. 点击 \+ 按钮创建新命令

4. 填写命令名称、描述和实际执行的 Shell 命令

5. 设置是否启用（启用后 AI 会优先推荐使用）

6. 点击「Save Command」保存

# 目录结构

```text
~/.config/nc-claw/
├── config.json              # 主配置（API 端点、模型、系统提示等）
├── api_pages.json           # API 页面配置
└── custom_commands.json     # 自定义命令配置
```


# 常见问题

## Q: API 调用失败怎么办？

- 检查 `api_key` 是否正确

- 确认网络可访问

- 检查模型名称是否合法

## Q: 命令执行无权限？

- 在自定义命令中添加 `sudo`（需确保用户有免密 sudo 权限）

- 或以 root 身份运行 NC\-Claw

## Q: 界面显示异常？

- 确保安装了最新版本的 GTK 4 和 libadwaita

- 尝试切换系统主题为 Adwaita

---

版本：v3\.1\.0 \| 依赖：Python 3\.x、GTK 4\.0、libadwaita 1\.x
