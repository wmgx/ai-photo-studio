# AI Photo Studio · AI 修图工作台

用网页管理照片相册、向本机 Codex 提交修图意见，并对每次修订进行对比审阅。

- **按目录建相册**：原片只读引用，同名 RAW 作为参考素材。
- **AI 修图**：每个相册可配置模型、推理强度和统一风格要求，支持一键逐张处理整册照片。
- **逐版审阅**：每次编辑单独保存并展示修改说明；对比默认原图加最新 N 个修订，数量可调；评论和标记绑定具体版本。
- **自主选片**：选择原图或任意修订版，按选择导出完整文件。

## 启动

需要 Python 3.10+。使用 AI 修改前，请安装并登录 [Codex CLI](https://developers.openai.com/codex/cli/)。

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/ai-photo-studio serve --library "/path/to/photos" --open-browser
```

服务仅监听本机，终端会显示访问地址。模型留空时使用 Codex 默认配置。**本地 CLI 不等于离线推理**，模型服务可能接收 Codex 读取的图片与任务上下文。

## 工程目录

```text
ai-photo-studio/
├── pyproject.toml             包信息、依赖与命令入口
├── README.md                  项目简介与启动方法
├── AGENTS.md                  AI 编辑与开发约定
├── src/ai_photo_studio/
│   ├── __main__.py            python -m ai_photo_studio 入口
│   ├── cli.py                 命令行
│   ├── server.py              本地 HTTP 服务
│   ├── executor.py            Codex 任务执行
│   ├── library.py             目录相册
│   ├── store.py               版本、评论、选片与导出
│   └── web/
│       ├── index.html         页面结构
│       ├── styles.css         页面样式
│       └── app.js             页面交互
├── tests/                     临时数据与合成图片测试
└── docs/
    └── usage.md               使用、CLI 与相册数据说明
```

程序代码与相册数据分别管理。每个相册的版本、评论和中间产物收纳在原有 `.photo-review/` 中；重命名项目无需搬动照片或迁移数据。

## 开发与验证

```sh
.venv/bin/python -m unittest discover -s tests
node --check src/ai_photo_studio/web/app.js
```

只使用 Python 标准库、Pillow 和原生网页资源。网页资源随 Python 包一起安装，无需前端构建步骤。

[完整使用指南](docs/usage.md) · [AI 编辑约定](AGENTS.md)
