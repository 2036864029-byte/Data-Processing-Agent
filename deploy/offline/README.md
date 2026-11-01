# 离线包骨架（P0/P1 交付形态）

面向无外网环境的政企客户。目录约定如下，发布时按版本打包为单个 tar：

```text
offline/
├── images/
│   └── dataagent-<version>.tar        # docker load 后得到运行镜像（含 uv.lock 锁定依赖）
├── wheels/
│   └── *.whl                          # uv export 产出 + 构建本项目的 wheel（pipx 单机安装用）
├── models/
│   └── ocr/                           # OCR / 检测模型文件（doctor 校验 models 目录）
└── fonts/
    └── *.ttf                          # 渲染 / Markdown 导出所需字体
```

安装步骤：

1. `docker load -i images/dataagent-<version>.tar`
2. `docker compose up -d`（compose 与 `deploy/docker-compose.yml` 一致，把 `*_URL` 指向外部后端即可多机）
3. 无 Docker 的单机环境（P1）：`uv sync --offline --frozen` 后 `dataagent up`

约束：

- 依赖只能来自 `uv.lock`（离线 wheel 与锁文件同源），禁止 `pip install` / `poetry` / `conda`。
- 模型 / 字体目录路径与 `dataagent doctor` 校验项一致；缺失时 doctor 给出显式修复建议。
