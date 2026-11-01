# ThemeShift / 主题移植

跨品牌手机主题移植工具，提供 **CLI** 与 **本地 Web 工作台**。当前实际支持把**小米 `.mtz` 的图标**移植为**荣耀 `.hnt`**；多品牌双向适配架构已准备好，但反向转换和其他品牌尚未实现。

> 本项目是非官方工具，与小米、荣耀无隶属关系。仓库不附带任何主题素材。锁屏、壁纸、字体和系统界面目前不迁移。

## 特性

- **轻量 Web**：Python + FastAPI / Uvicorn 后端，原生 HTML/CSS/JavaScript 前端；无 Node 构建链、数据库、Redis、Celery 或外部 CDN。
- **共用业务核心**：CLI 与 Web 调用同一 `MigrationService`，避免两套转换逻辑。
- **可扩展品牌**：版本化平台档案可同时注册读取器与写出器，显式声明模块能力和方向映射；兼容原 v1 档案。
- **可扩展中间模型**：版本化 IR、模块资源槽与 `ModuleTransformer` 接口为后续壁纸等模块预留边界，不盲目直通未知资源。
- **保留图标功能**：系统包名映射、第三方直通、登记的动态资源、`192×192` / `172×172` 安全区及四张荣耀预览图。
- **可调图标边距**：Web 滑块与 CLI `--icon-margin` 都能调整内容区留白，范围由目标平台真实画布决定；写出器回读自检会确认图标未越出安全区。
- **渲染有并行**：图标按 CPU 核数多线程渲染（可用档案 `icon.workers` 覆盖），PNG 编码跳过最慢的 `optimize` 搜索；实测 300 图标约快 1.6 倍，写真类图标约快 2 倍。
- **输出可验证**：确定性压缩与回读自检；自检失败不发布新主题，CLI 返回非零退出码。
- **受限上传与隔离**：每任务独立目录，有界队列、上传/解压/图像限额、路径穿越防护和过期清理。

## 安装与 Web 启动

需要 Python 3.10+。在项目根目录运行：

```bash
python -m pip install ".[web]"
python -m themeshift serve
```

浏览器打开 **<http://127.0.0.1:8765>**。界面分三步：① 文件与方向 → ② 图标设置（边距、主题信息、只分析）→ ③ 任务结果与下载。平台能力收在侧栏的独立面板中，不会与表单堆在同一页。

还没有 `.mtz` 素材时，页面“选择源主题”下方提供 [mtztheme.com](https://www.mtztheme.com/) 的下载入口。该站是第三方站点，内容与素材授权由它负责；本工具只会把文件发送给当前运行的服务，不会向该站上传任何内容。

支持拖放、真实上传进度、后台转换状态、失败原因、只分析模式、最近任务恢复及清理。目标平台支持时还会显示“图标边距”滑块，可直接预览数值对应的内容区尺寸；方向选项来自服务端真实能力，不展示未实现品牌。

推荐使用独立虚拟环境（Windows PowerShell）：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install ".[web]"
.\.venv\Scripts\python.exe -m themeshift serve
```

默认仅监听本机；这是可信单用户工具，**不要直接暴露公网**。默认 128 MiB 上传、2 个转换线程、8 个在途任务、32 个保留任务、完成后保留 1 小时。重启不恢复任务。详见 [Web/API 与部署说明](<docs/web.md>)。

仅使用命令行时无需安装 Web 依赖：

```bash
python -m pip install .          # 核心仅依赖 Pillow
python -m pip install ".[xlsx]"  # 可选 Excel 包名清单
```

源码开发安装：

```bash
python -m pip install -e ".[web,dev,xlsx]"
python -m themeshift --help
```

> 注意：直接 `pip install .` 会安装一份拷贝；之后修改源码，`python -m themeshift` 仍会加载 site-packages 里的旧版本。开发时请用 `-e`（可编辑安装），改完代码后重启 `serve` 即可生效。

## 快速开始

```bash
# 最常用：自动识别平台、读取主题名与作者，输出到 outputs/
themeshift 超拟圆.mtz

# 显式迁移，并覆盖元数据
themeshift migrate 超拟圆.mtz --name "超拟圆" --author "Lilfox" --version-number 1.0.1

# 调整图标边距：内容区更小、留白更多（荣耀画布 192 px，默认 10 px）
themeshift migrate 超拟圆.mtz --icon-margin 6

# 只分析，不打包
themeshift migrate 超拟圆.mtz --dry-run

# 查看源主题信息
themeshift inspect 超拟圆.mtz

# 列出可用平台与映射
themeshift platforms
```

输出会显示读取、映射、生成和校验阶段，成功时给出主题与映射报告路径。自检失败返回退出码 `5`，不会发布新产物。只分析模式不会生成下载文件。

## 命令参考

| 命令 | 说明 |
| --- | --- |
| `themeshift <input>` | `migrate` 的简写 |
| `themeshift migrate <input>` | 执行移植 |
| `themeshift inspect <input>` | 读取源主题并打印信息 |
| `themeshift platforms` | 列出读写能力与实际可用方向 |
| `themeshift serve` | 启动轻量本地 Web 工作台 |

`migrate` 常用参数：

| 参数 | 说明 |
| --- | --- |
| `-t, --to` | 目标平台，默认 `honor` |
| `-f, --from` | 源平台，默认按扩展名识别 |
| `--name` / `--name-en` | 覆盖中/英文主题名 |
| `--author` | 覆盖设计师 |
| `--version-number` | 覆盖版本号 |
| `--brief` / `--brief-en` | 覆盖中/英文简介 |
| `--packages` | 限定系统应用目标包名（`.xlsx` / `.json`） |
| `-o, --out` | 输出路径 |
| `--modules` | 选择已实现的共同模块，例如 `--modules icons` |
| `--icon-margin` | 图标边距（像素），范围由目标平台决定；荣耀为 `0–72`，默认 `10` |
| `--workdir` | 中间目录父目录；每次运行创建并保留独占子目录 |
| `--keep-work` | 保留自动创建的中间目录 |
| `--dry-run` | 只统计，不打包 |
| `--data-dir` | 覆盖数据目录（也可用 `THEMESHIFT_DATA`） |
| `-q` / `--debug` | 静默 / 调试日志 |

## 输出结构

`.hnt` 是一个 zip 包：

```text
<主题名>_荣耀图标移植.hnt
├── description.xml
├── icons                    # 内层 zip
│   ├── com.honor.browser.png
│   ├── com.tencent.mm.png
│   └── dynamic_icons/<package>/<file>.png
└── preview
    ├── icon_small.jpg       # 510×345, 3 列 × 2 行
    ├── icon_small_1.jpg     # 640×640, 2×2
    ├── preview_icons_0.jpg  # 1080×2160
    └── preview_icons_1.jpg  # 1080×2160
```

同时在输出文件旁生成 `<输出文件主名>_荣耀图标包名映射.txt`，记录系统应用映射、第三方直通和迁移警告。Web 界面分别提供主题和报告下载链接。

## 荣耀图标规范

| 项目 | 要求 |
| --- | --- |
| 画布 | `192×192`，透明背景 |
| 内容区 | `172×172`，等比缩放、居中 |
| 安全边距 | 四边 ≥ 10 px |
| `description.xml` | UTF-8 无 BOM、Unix `LF` |
| `extraContentFlag` | `"5"`（单独上架的图标） |

细节见 [荣耀图标规范](<docs/honor-icon-spec.md>)。

## 项目结构

```text
themeshift/
├── src/themeshift/
│   ├── cli.py            # CLI 参数与输出
│   ├── service.py        # 共用转换服务、能力协商、隔离与发布
│   ├── models.py         # 版本化中间模型和通用自检
│   ├── profiles.py       # v1/v2 档案校验与独立读写能力
│   ├── registry.py       # 可信 module:Class 适配器装载
│   ├── repository.py     # 平台数据目录
│   ├── mapping.py        # 显式有向包名/资源映射
│   ├── imaging.py        # 有界图片解码、归一化及预览
│   ├── packaging.py      # 有界安全归档与确定性打包
│   ├── platforms/        # 厂商读写实现与模块转换协议
│   ├── data/             # profiles/ 与 mappings/ JSON
│   └── web/
│       ├── app.py        # 同源 API 与静态资源
│       ├── jobs.py       # 有界任务队列、下载和过期清理
│       ├── settings.py   # 服务限额
│       └── static/       # 原生 HTML / CSS / JavaScript
├── tests/                # unittest + 可选真实 ASGI API 测试
├── scripts/              # 可选真实浏览器冒烟检查
├── docs/
└── examples/
```

代码描述行为，JSON 描述平台知识。完整分层与后续演进见 [架构说明](<docs/architecture.md>)。

## 扩展平台

1. 在 [平台档案目录](<src/themeshift/data/profiles/>) 增加 schema v2 档案，分别声明 `adapters.source` / `adapters.target`、实现入口和模块能力。一个品牌可同时具备读写角色。
2. 为每个方向分别增加 `<source>_to_<target>.json`；**不会自动倒置已有映射推测反向支持**。
3. 实现 [适配器协议](<src/themeshift/platforms/base.py>)，读出中间模型、写出目标并返回通用自检结果。
4. 全新模块还需实现并注入 `ModuleTransformer`；只声明 `wallpapers` 等名称并不产生转换能力。
5. 补充该方向的真实格式测试，通过后即可由 CLI 和 Web 发现能力。

可以通过 `--data-dir` / `THEMESHIFT_DATA` 使用可信外部平台包；该目录替换整个数据根，不与内置档案自动合并。完整步骤见 [扩展指南](<docs/adding-a-platform.md>)。

## 已知限制

- 只移植图标；锁屏、壁纸、状态栏、设置等模块不会迁移。
- 调整边距会等比缩小图标内容区：留白越多，曲线可用的像素越少；中等倍率使用 Lanczos，缩小超过 3.5 倍改用面积平均，叠加采用 source-over 以保留原始抗锯齿。
- 为提速，PNG 使用默认压缩级别而非 `optimize`，图标包会比逐图标最优压缩大约 2–4%。
- 依赖源主题的目录约定（`icons` 内层包 + `res/drawable-*`）。结构差异较大时会走兜底扫描。
- 动态图标按映射表逐文件对应，只覆盖已登记的角色（日历底、时钟指针、天气底等）。
- 不对图标做圆角遮罩或自动重绘，保留原始图形。

## 开发

```bash
python -m pip install -e ".[web,dev,xlsx]"
python run_tests.py          # 任意目录可用
python -m unittest discover -s tests -t .
ruff check src tests scripts
node --check src/themeshift/web/static/app.js
python -m build
```

Web 未安装时相关测试会跳过；验证完整功能应安装上述 extras。可选真实浏览器检查见 [Web 开发验证](<docs/web.md#7-开发验证>)。

贡献流程与代码风格见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 免责声明

- 本项目与小米、荣耀及其关联公司无关，未获其授权或背书。
- Xiaomi、MIUI、HONOR 等名称与商标归各自权利人所有。
- 本仓库只包含工具代码与公开的包名映射数据，**不包含任何主题美术素材**。
- 使用本工具移植、分发主题时，请自行确认对原始素材的授权。

## License

[MIT](LICENSE)

---

## English

`themeshift` migrates icon themes between mobile platforms. It currently converts
Xiaomi `.mtz` icon sets into HONOR `.hnt` packages: system packages are remapped
through JSON data, third-party packages pass through unchanged, every icon is
normalized onto a 192×192 transparent canvas with a 172×172 safe area, and the
HONOR preview images and `description.xml` are generated and self-verified.

Platform knowledge lives in JSON profiles and explicit directional mappings.
Versioned profiles allow independent readers and writers for the same vendor;
a neutral IR and module-transformer interfaces prepare future full-theme work.
Only Xiaomi-to-HONOR icons are implemented today.

Install `.[web]` and run `python -m themeshift serve` for the local workspace at
`http://127.0.0.1:8765`. The FastAPI backend and dependency-free browser UI share
the same Python conversion service as the CLI. No database, Redis or frontend
build chain is required; jobs are bounded, temporary and not restart-persistent.
