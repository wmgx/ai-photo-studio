# 使用指南

以文件夹为相册的本地照片审阅工具。网页负责选片、逐版评论和审阅；本机 Codex CLI 根据意见生成候选图，程序登记为新版本。原图和每次修订分别保留，最终可以选择原图或任意历史版本导出。

## 快速开始

在项目根目录执行安装命令。后续示例中的 `ai-photo-studio` 需先运行 `source .venv/bin/activate`，也可直接使用 `.venv/bin/ai-photo-studio`。

需要 Python 3.10+、Pillow。AI 修改另外需要安装并登录 [Codex CLI](https://developers.openai.com/codex/cli/)。

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/ai-photo-studio serve --library "/path/to/photos" --open-browser
```

程序打印本地地址，默认仅监听 `127.0.0.1`。保持终端运行；停止后重新执行同一命令即可继续审片。可用 `--port 8765` 固定端口。

每个直接包含照片或视频的目录是一个相册，子目录单独列出。打开相册时登记原片；重新打开会登记新加入的照片，已有版本不会被替换。隐藏目录、软链接以及 `versions`、`exports` 目录不会被扫描。

支持 JPEG、PNG、TIFF、MPO；同名 RAW 文件作为参考素材关联。视频保留原文件，能否在网页播放取决于浏览器的解码支持。仅含 RAW 的目录暂不列为可预览相册，需要先提供 JPEG 等预览原片。自动导入的静态照片暂归为“风景”，分类也可在 CLI 导入时指定。

## 网页工作流

1. 选择相册，使用方向键移动，Enter 打开照片。
2. 查看完整原图和历史版本；原图预览保留全部画面。选择评论针对的版本，填写意见，可标记具体位置。
3. “仅保存评论”用于记录；“保存并提交修改”启动本机 Codex。模型名称和推理强度在“Codex 设置”中按相册保存，留空沿用 CLI 默认配置。
4. Codex 在任务目录生成候选，程序检查文件、父版本及当前版本后登记修订。审阅通过后，相关意见才成为已解决。
5. 为每张照片选择交付版本，可选原图；导出按钮将已选文件复制到新目录并生成校验清单。

模型和推理档位是否可用取决于你当前的 Codex 配置和账户。程序复用 CLI 的现有登录，不保存 API 密钥。调用方式使用 [Codex 非交互执行](https://developers.openai.com/codex/noninteractive/)及结构化输出。

**本地调用不等于离线推理。** 页面与照片处理脚本在本机运行，Codex 仍按你的模型服务配置发送任务上下文，可能包括它读取的图像。任务要求用本地工具处理照片；本程序没有云端图像编辑接口。它不是离线隐私隔离工具，请按照片的隐私要求配置 Codex。

## 文件组织

程序仓库只存代码。照片、日志、评论和候选留在相册数据目录中：

```text
photos/
├── trip-a/                         一个相册
│   ├── sample.jpg                  原片，只读引用
│   ├── sample.nef                  同名 RAW 参考
│   └── .photo-review/
│       ├── project.json            相册偏好与模型设置
│       ├── versions/照片ID/版本ID/   正式修订，逐版保留
│       ├── exports/导出批次/         已选文件与导出清单.csv
│       └── .review/
│           ├── state.sqlite3       版本、评论、选片记录
│           ├── work/任务ID/         候选、脚本、蒙版、任务输入及执行日志
│           └── cache/              可重建预览
├── trip-b/                         另一个相册
└── .photo-review-server/            本地服务日志和地址
```

评论和标记固定绑定版本 ID。交付选择独立于最新修订，新增版本不会自动替换已选版本。候选、缓存和日志不进入照片导出。文件哈希、尺寸和操作 ID 由程序生成；同一发布操作重试只登记一次。旧任务遇到当前版本变化会停止发布，候选保留供检查。

原片、历史版本和工作材料不会自动清理。原片路径需保持可用；目前数据记录含绝对路径，暂不支持直接移动相册目录后无缝继续。

## AI 与 CLI 接口

除网页外，所有 CLI 结果均为 JSON。激活虚拟环境后查看 `ai-photo-studio --help`。也可以创建独立数据批次，适合先选图再导入：

```sh
ai-photo-studio --batch "/path/to/review-data" init --name "示例相册"
ai-photo-studio --batch "/path/to/review-data" import "/path/to/sample.jpg" --photo-id photo001 --category 人物 --source "/path/to/sample.nef"
ai-photo-studio --batch "/path/to/review-data" serve --open-browser
```

初始化支持 `--preferences-file`，读取 UTF-8 JSON 对象，例如：

```json
{"处理方式":"本地处理","人物":"保持真实身份和自然皮肤质感","风景":"依据实际季节、天气调色","保护":"原片只读；文字准确；完整原图预览不裁切"}
```

独立编辑 agent 先阅读 [AGENTS.md](../AGENTS.md)，用 `work` 领取指定版本的材料，在返回的 `workDir` 中编辑，用 `publish` 发布，再通过 `comments reply` 回填说明。目录相册的 `--batch` 指向该相册的 `.photo-review`。

```sh
ai-photo-studio --batch "/path/to/review-data" comments list --status open
ai-photo-studio --batch "/path/to/review-data" work --photo-id photo001 --version-id BASE_VERSION --comment-id COMMENT_ID
ai-photo-studio --batch "/path/to/review-data" publish --photo-id photo001 --parent-id BASE_VERSION --expected-current-id EXPECTED_CURRENT_VERSION --job-id JOB_ID --operation-id UNIQUE_EDIT_ID --candidate "/returned/workDir/candidate.jpg" --label "曝光调整" --summary "提亮主体，保留高光" --comment-id COMMENT_ID
ai-photo-studio --batch "/path/to/review-data" comments reply COMMENT_ID --text "修订已登记，请审阅。" --result-version-id NEW_VERSION_ID
```

**网页启动的 Codex 任务只负责写候选和结构化结果。** 后台程序负责登记版本与回复，agent 不需要自行调用发布命令。任务在分配的工作目录通过 `workspace-write` 沙箱运行，不给相册原片目录额外写权限；不会自动申请提升权限。已有 Codex 配置、插件和工具仍需由使用者信任和管理。

## 验证与发布隐私

```sh
python3 -m unittest discover -s tests
```

验证使用临时目录和合成图片。仓库不包含用户照片、评论、任务日志、账户凭据或真实本机路径。`.gitignore` 排除常见相册数据及媒体文件；发布前仍应检查暂存文件与 Git 提交身份。导出的照片保留所选文件的字节和元数据，分享照片前请自行检查 EXIF 位置信息。
