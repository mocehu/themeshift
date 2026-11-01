# 架构与能力边界

ThemeShift 把平台格式、转换业务和调用入口分开：**适配器负责读写，服务层负责协商与编排，CLI / Web 只负责交互。** 可扩展的协议不等于已经实现所有品牌或全主题转换。

## 1. 当前实际能力

| 内置平台 | 读取 | 写出 | 声明的模块 |
| --- | --- | --- | --- |
| 小米 / MIUI | `.mtz` | 无 | `icons`、`dynamic_icons` |
| HONOR | 无 | `.hnt` | `icons`、`dynamic_icons` |

目前唯一内置方向是 **小米 → 荣耀图标**，由[小米档案](<../src/themeshift/data/profiles/xiaomi.json>)、[荣耀档案](<../src/themeshift/data/profiles/honor.json>)和[该方向的映射](<../src/themeshift/data/mappings/xiaomi_to_honor.json>)共同定义。

- 静态图标按显式包名映射或第三方原包名直通；不自动重绘图标。
- 动态图标只迁移映射中登记的资源角色，不保证不同厂商的动态行为等价。
- 壁纸、锁屏、`system_ui` 等全主题模块尚未实现；没有内置反向移植，也没有其他品牌的现成适配器。

## 2. 分层与依赖方向

```text
CLI / Web / Python 调用方
          │
          ▼
MigrationService ── DataRepository / profiles / registry
          │            │ 受信任的档案、实现类与有向映射
          ├─ plan：检查来源、目标、模块和映射方向
          ├─ SourcePlatform.load → SourceTheme（IR v1）
          ├─ 内置图标映射 + 已注入的 ModuleTransformer
          │                    → NormalizedTheme（IR v1）
          ├─ TargetPlatform.build → 暂存产物 + BuildReport
          └─ 校验通过 → 逐文件原子发布主题与映射报告
```

[服务层](<../src/themeshift/service.py>)不导入 CLI、`argparse`、FastAPI、Uvicorn 或具体厂商实现；厂商类由注册器根据档案在运行时加载。读取器不需要了解目标平台的文件布局，写出器不需要了解源主题容器。

| 层 / 模块 | 职责 |
| --- | --- |
| [service.py](<../src/themeshift/service.py>) | `MigrationRequest` / `MigrationPlan` / `MigrationResult`、元数据覆盖、能力协商、隔离、校验和发布 |
| [models.py](<../src/themeshift/models.py>) | 平台中立的 IR、资源描述、统计和构建报告 |
| [base.py](<../src/themeshift/platforms/base.py>) | 读取器、写出器、模块转换器协议 |
| [profiles.py](<../src/themeshift/profiles.py>)、[repository.py](<../src/themeshift/repository.py>)、[registry.py](<../src/themeshift/registry.py>) | 档案版本兼容、数据定位与验证、独立角色装载 |
| [mapping.py](<../src/themeshift/mapping.py>) | 执行有向系统包名映射、第三方直通及登记的动态资源映射 |
| [xiaomi.py](<../src/themeshift/platforms/xiaomi.py>)、[honor.py](<../src/themeshift/platforms/honor.py>) | 当前具体厂商的读取 / 写出约定 |
| [packaging.py](<../src/themeshift/packaging.py>)、[imaging.py](<../src/themeshift/imaging.py>) | 有界归档处理、文本与 ZIP 写入、图像解码及布局工具 |
| [cli.py](<../src/themeshift/cli.py>)、[Web API](<../src/themeshift/web/app.py>) | 将参数或 HTTP 表单转换为服务请求，并呈现结果 |
| [JobManager](<../src/themeshift/web/jobs.py>) | Web 专用的任务容量、工作线程、状态快照、下载与过期回收 |

平台尺寸、布局、入口和应用映射位于[平台档案目录](<../src/themeshift/data/profiles/>)与[映射目录](<../src/themeshift/data/mappings/>)。档案中的 `module:Class` 会执行受信任的 Python 实现，不是上传主题可以自行提供的插件，也不是沙箱。

## 3. 两种版本：档案 schema 与中间模型 IR

这两个版本号彼此独立：**当前档案支持 schema 1 / 2，当前 IR 使用 schema 1。**

### 版本化中间模型

- `SourceTheme(schema_version=1)`：元数据、`StaticIcon` 列表、`DynamicAsset` 列表、来源信息，以及按模块分组的资源。
- `NormalizedTheme(schema_version=1)`：目标包名到图标路径的字典、已映射的动态资源、统计、报告行、警告和目标模块资源。
- `ThemeResource`：`key`、`path`、`media_type`、`attributes`。它提供通用资源容器；键和属性的业务含义必须由对应转换器及适配器明确约定。

服务拒绝非 v1 的 `SourceTheme`，不会猜测如何升级未知 IR。新写出器应按约定版本消费 `NormalizedTheme`。IR 中的资源路径服务于本次转换，不是持久化任务协议；未保留工作目录时，运行结束后不能继续使用其中的临时文件。

`modules` 中的任意文件不会被自动复制到目标。平台中立的容器已经存在，但完整的跨厂商资源语义标准尚未实现。

### 独立双角色档案

schema 2 的同一品牌档案可在 `adapters.source`、`adapters.target` 中声明读取器、写出器或两者，每个角色都有独立的 `implementation` 和非空 `modules`。两个类不必互相继承，也不要求一个类同时实现读写。

旧 schema 1（包括未写 `schema_version` 的档案）的 `kind` / `implementation` 仍兼容：只声明一个角色，其能力按旧约定视为 `icons` 与 `dynamic_icons`。不能把 v1 的顶层声明和 v2 的 `adapters` 混用。具体格式见[新增平台指南](<adding-a-platform.md>)。

## 4. 模块协商与有向映射

一个方向可转换的模块为：

```text
源读取器模块 ∩ 目标写出器模块 ∩ 服务已实现的模块
```

服务已实现的模块由内置 `icons` / `dynamic_icons` 加上构造 `MigrationService` 时注入的转换器名称组成。

- 未显式选择模块时，使用该交集；显式选择时，必须是交集的非空、无重复子集。
- 还必须存在与请求方向完全一致的 `<source>_to_<target>.json`，文件内容的 `source` / `target` 也必须匹配。
- `catalog()` 只列出具有适配器、显式方向映射和共同可转换模块的路由。多个读取器共用扩展名时，自动来源识别会拒绝歧义，调用方必须指定来源。
- A → B 不代表 B → A。包名候选、合并关系和动态角色可能有损，**不得通过倒置已有映射推测反向支持**。同品牌读写也需要自己的显式方向映射。

`ModuleTransformer.transform(source, target, mapping)` 是已实现的通用扩展点：接收源 IR、目标 IR 和当前方向的 `PackageMap`，显式解释并填充目标模块资源，可记录报告和警告。转换器需通过 `MigrationService(..., transformers={...})` 注入；不能覆盖内置的两个图标模块。

仅在档案中增加 `wallpapers` 等名称不会产生转换能力。默认 CLI / Web 服务没有这些转换器；`--modules` 只选择能力，不安装或实现能力。

### 渲染选项：以图标边距为例

写出器可选择声明 `build(theme, out_path, work_dir, options=None)`，服务只在检测到该参数时才传递已校验的渲染选项；旧的三参数写出器继续按原协议调用，不会被要求改写。

- 选项是否可调由服务端能力决定：档案必须声明自洽的 `icon.canvas` / `icon.content`，且写出器接受 `options`。`/api/platforms` 才会返回该目标平台的 `icon_margin`（`min`、`max`、`default`、`canvas`）。
- 边距表示画布边缘到内容区的距离，内容区为 `canvas − 2 × margin`；至少保留 `max(32, canvas / 4)` px。荣耀默认 `10`，可调 `0–72`。
- 服务在创建工作目录前校验类型与范围并给出 `UsageError`；写出器再次校验，避免依赖调用方。
- 不支持该选项的目标平台收到参数时明确报错，而不是静默忽略；前端只在服务声明能力时显示滑块。
- 选项只影响图像绘制，不改变主题包结构，也不进入 IR。`BuildReport.content_size` 与 `icons_outside_content` 记录实际内容区与越界图标数，越界即自检失败。

## 5. 转换生命周期与隔离

1. 检查输入、档案、角色、模块和显式方向；不支持的请求在创建本次工作目录前拒绝。
2. 每次 `migrate()` 使用 `mkdtemp` 创建独占的 `themeshift-*` 子目录，再让读取器在其中工作。相同文件名和并行任务不会共享固定的解包目录。
3. 校验源 IR 版本及元数据，应用内置图标映射和协商后的自定义转换器；没有可输出资源时明确报错。
4. `dry_run` 到此返回分析统计与警告：不构建主题、不生成映射报告文件、不执行目标图像归一化或输出包自检。
5. 正常模式让写出器生成暂存文件并回读自检，通过后进入发布阶段。
6. 默认只删除本次独占子目录；不会清空用户提供的父目录，也不会删除其他运行的工作目录。

保留策略需要区分入口：

| 调用方式 | 行为 |
| --- | --- |
| 直接调用服务，`work_dir=parent` | 仅指定父目录；是否保留由 `keep_work` 决定 |
| CLI `--workdir parent` | 创建并保留该父目录下的新子目录，不直接把父目录当作本次工作区 |
| CLI `--keep-work` | 保留自动创建的本次工作目录 |
| Web 任务 | 每任务独立目录；转换结束后清理原始上传和中间工作文件，结果按任务 TTL 保留 |

## 6. 通用自检与逐文件原子发布

新适配器使用 `BuildReport(checks={...}, issues=[...])`：

- `issues` 非空即失败；`checks` 非空时，每一项必须严格为 `True`。
- 空 `checks` 保留旧荣耀字段的兼容判定。新平台不应为了通过自检而伪填荣耀专用的预览、描述换行或 `extra_content_flag` 字段。
- 写出器必须实际生成传入的暂存 `out_path`。自检不通过或暂存文件缺失会抛出 `BuildValidationError`；**未发布新输出，不是仅打印警告继续成功**。此时已有最终主题和报告保持原样。

荣耀的画布、安全区、预览图和描述文件检查属于荣耀写出器的格式要求，而不是所有品牌的通用 IR 不变量；详见[荣耀图标规范](<honor-icon-spec.md>)。

通过校验后，服务先将每个产物复制到最终目标目录中的唯一临时文件，再用 `os.replace` 发布：

- 保证的是**单个文件的原子替换**，不是主题和报告两个文件的跨文件事务。后一次发布若遇到 I/O 错误，前一个文件可能已发布。
- 输入文件不能被输出主题或报告覆盖；主题与报告也不能使用相同路径。
- 并行 CLI 调用仍应选择不同的最终输出路径；工作目录隔离不等于最终同名文件具备版本管理。

内置 ZIP 工具固定时间戳和权限；条目顺序由调用方提供，新写出器应稳定排序后写入。确定性构建还取决于输入、配置、适配器及图像处理环境，不是服务层对任意第三方格式的自动保证。

## 7. 轻量 Web 的边界

Web 默认监听 `127.0.0.1`，以单 HTTP 进程运行，面向本机单用户 / 可信使用场景；不是带账户隔离的多租户服务。转换使用内存队列和 `ThreadPoolExecutor`，并通过 `max_pending`、`max_jobs`、`workers` 限制待处理任务、总任务和并发量，**无需 Redis 或数据库**。

- 任务公开状态为 `queued`、`running`、`succeeded`、`failed`；上传预留状态不对外作为可查询任务暴露。
- 任务状态仅在内存中，结果文件放在临时任务目录；两者均不支持跨进程重启恢复，旧任务编号和下载链接随服务重启失效。
- 终态完成后才开始计算结果 TTL；下载通过任务专属 URL 获取。正在下载的文件有租约保护，活动或正在下载的任务不能直接清理。
- 当前没有硬转换超时，也不能安全强杀正在运行的 Python 工作线程。上传超时、归档预算和任务 TTL 不等于转换执行超时。
- 若以后需要持久化队列、进程隔离或多机调度，可替换传输层的 `JobManager`，不必把 HTTP / 队列依赖放进服务层；这些能力目前未实现。

上传、归档与图像安全预算，以及同源请求和部署边界，见 [Web 工作台说明](<web.md>)。

## 8. 错误与验证

CLI 按[异常定义](<../src/themeshift/errors.py>)映射退出码；Web 将输入错误变成 HTTP 错误或任务错误，不把 CLI 退出码当作 HTTP 状态码。

| 退出码 | 含义 |
| --- | --- |
| `0` | 成功，包括成功的只分析 |
| `1` | 未预期异常 |
| `2` | `UsageError`：参数、路径或当前请求不可用 |
| `3` | `SourceFormatError`：源包或源 IR 不受支持 |
| `4` | `ConfigError`：档案、实现入口或方向映射错误 |
| `5` | `BuildValidationError`：输出自检失败，未发布新输出 |
| `130` | 用户中断 |

[协议测试](<../tests/test_profiles.py>)验证 schema 1 / 2、双角色和独立方向；[服务测试](<../tests/test_service.py>)用纯文本 toy 适配器验证无传输层依赖、通用自检、显式模块转换、失败不发布、工作目录隔离和并发行为。toy 的壁纸转换测试只证明扩展机制，不代表已经实现真实品牌的壁纸迁移。[端到端测试](<../tests/test_end_to_end.py>)覆盖当前实际的小米 → 荣耀路径。

## 9. 后续可分期演进，但尚未实现

1. 定义跨厂商应用身份、资源角色、尺寸 / 状态语义和有损转换策略；当前 `PackageMap` 仍是显式厂商包名与动态资源映射，不是完整的规范化语义映射层。
2. 按模块增加真实读取器、转换器、写出器与各目标格式自检，再将厂商数据接入上述语义；不能以文件复制代替壁纸、锁屏或系统 UI 的语义转换。
3. 为每个品牌、方向和模块建立兼容性测试；只有实现与验证完成后才声明能力，必要时再设计新的 IR 版本及迁移方案。
