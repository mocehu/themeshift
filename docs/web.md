# Web 工作台、API 与部署

## 1. 技术选型

- **业务核心：Python 3.10+ / Pillow**。复用现有图标、压缩包和包名映射实现，不通过子进程调用 CLI，也不另写一套 Go / Node 转换器。
- **后端：FastAPI + Uvicorn**。异步接收有大小限制的上传，转换交给受控线程池；HTTP 层只把请求转换为共享 `MigrationService` 的参数。
- **前端：原生 HTML / CSS / JavaScript**。由同一个服务提供静态资源，无 npm、构建工具、前端运行时框架、CDN、远程字体或第三方上传服务。视觉采用 iOS 风格：`#f2f2f7` 分组底色、白色卡片、细线分隔、系统字体与克制的蓝色强调；不使用渐变、毛玻璃或大面积阴影，信息排布紧凑，一屏尽量多放内容。强制颜色与“减少动态效果”模式下仍有对应回退。
- **任务：内存元数据 + 本地独立目录**。无数据库、Redis、Celery；单进程即可运行，代价是不支持服务重启恢复和多机共享任务。

核心只依赖 Pillow，Web 依赖为可选安装项，具体版本范围见[项目配置](<../pyproject.toml>)。

## 2. 安装与运行

在项目根目录：

```bash
python -m pip install ".[web]"
python -m themeshift serve
```

打开 **<http://127.0.0.1:8765>**。不能通过双击 HTML 或另开前端静态服务器使用完整功能；页面与 API 应保持同源。

Windows 建议使用虚拟环境，无需更改 PowerShell 执行策略：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install ".[web]"
.\.venv\Scripts\python.exe -m themeshift serve
```

源码开发可用 `python -m pip install -e ".[web,dev,xlsx]"`；不安装项目时，需自行把 `src` 加入 `PYTHONPATH`。

### 常用启动参数

```bash
python -m themeshift serve --port 8765 --job-workers 2 --max-upload-mb 128
python -m themeshift serve --storage-dir ./runtime --retention-minutes 30
python -m themeshift serve --data-dir ./my-platform-pack
```

| 参数 | 默认值 | 含义 |
| --- | --- | --- |
| `--host` | `127.0.0.1` | 仅本机监听 |
| `--port` | `8765` | HTTP 端口 |
| `--storage-dir` | `.themeshift-web` | 本实例临时会话目录的父目录 |
| `--job-workers` | `2` | 同时运行的转换任务数，最多 16；每个任务内部再按 CPU 核数并行渲染图标（上限 8） |
| `--max-upload-mb` | `128` | 单个主题上传字节上限，以 MiB 计算 |
| `--max-pending` | `8` | 上传中、排队中、运行中任务的合计上限 |
| `--max-jobs` | `32` | 包括已完成结果在内的全部任务上限 |
| `--retention-minutes` | `60` | 从任务完成开始计算的结果保留时间 |
| `--allow-host` | 额外列表为空 | 允许访问的额外 Host 主机名，不含端口；可重复 |
| `--data-dir` | 内置档案 | 服务端可信平台数据目录，替换而非合并内置目录 |

`max_pending` 不得大于 `max_jobs`。HTTP 固定使用单个 Uvicorn 进程；不要在外部通过多个 ASGI worker 运行这套内存任务存储。

## 3. 使用流程与能力范围

界面按三步组织，同一时间只展示一步，避免把表单、任务和平台信息堆满整页；侧栏的“平台能力”在独立弹窗中查看，不会离开当前步骤。

1. **文件与方向**：选择或拖入一个源主题文件，并在同一屏确认来源与目标平台。界面检查扩展名、空文件、大小及 MTZ 基础签名；服务端仍验证真实容器、资源与安全预算。文件就绪后可进入下一步。没有素材时，上传区下方的“mtztheme.com”入口会在新标签页打开第三方主题站——它由第三方运营，本工具不会向其发送任何文件，素材授权需自行确认。
2. **图标设置**：目标平台支持时显示“图标边距”滑块，它调整图标内容区与画布边缘的距离，范围与默认值来自该平台的真实画布配置；小米 → 荣耀时画布为 192 px，默认边距 10 px（内容区 172 px），可调 0–72 px。边距越大图标越小，设得过小可能被系统圆角裁切；该选项只影响图标绘制，不改变主题包结构。
3. 可在同一屏覆盖名称、英文名、作者、版本和中英文简介。短字段最多 200 字符，简介最多 2000；留空沿用源数据，源没有提供时由目标适配器采用默认值。
4. 提交后自动进入**任务结果**一步，显示真实上传进度，再显示服务端转换阶段。阶段百分比不是预计剩余时间，也不是逐图标进度。可从侧栏“当前任务”随时回到此步。
5. 成功后查看映射统计、自检与警告，分别下载主题和 UTF-8 映射报告。清理任务会删除其服务端文件，需要二次确认；“新建任务”会回到第一步。

**当前只有小米 `.mtz` → 荣耀 `.hnt` 图标方向**。静态图标、已登记的动态资源可迁移；壁纸、锁屏、字体、系统 UI 和其他品牌 / 反向方向尚未实现。动态图标资源映射不保证动态行为等价。

“只分析”仍会读取主题、应用映射并统计，但不会生成主题 / 报告下载，也不会执行目标图像归一化和最终输出自检；分析成功不等于目标包已经通过验证，边距也在实际构建时才生效。

## 4. 任务生命周期和隐私

- 浏览器仅把最近一次任务 ID 写入 `localStorage`；刷新后可在同一服务进程内恢复查询。不会在浏览器存储上传原件。
- 每个任务使用不可预测的 UUID，上传文件只写入服务生成的路径。用户不能指定服务器文件路径、工作目录或动态加载的 Python 模块。
- 上传、排队、运行都占用在途额度；在解析 multipart 之前预留额度。容量不足返回 `429` 与 `Retry-After`，不无限排队。
- 转换结束即清理原始上传和中间文件。已完成任务与产物到期清理，后台约每 30 秒检查一次（短保留期更频繁），查询和新任务预留也会触发清理。
- 正在下载的产物有租约保护；运行中或正在下载的任务不能删除，返回 `409`。下载结束后再回收。浏览器时间只作提示，以服务端结果为准。
- 正常关闭服务会拒绝新任务、取消尚未执行的排队任务、等待已运行转换和下载结束，随后删除本实例拥有的会话目录，不会清空用户的存储父目录。
- **不持久化任务状态**。重启后旧 ID / 下载链接失效。进程崩溃或被强杀可能残留 `session-*` 目录；确认相关进程已经停止后，由管理员检查并清理这些会话目录。不会自动删除不属于当前实例的文件。
- 没有全局任务列表。任务 ID / 下载 URL 相当于访问凭据，知道 ID 的人可查询 / 下载该任务；这不是用户账户隔离，不要分享敏感链接。
- 如果创建响应在断网时丢失，服务器可能已经收到任务。界面不会自动重复上传，会要求用户明确确认。当前没有幂等请求键或丢失 ID 的找回机制。

## 5. HTTP API

统一前缀 `/api`；接口结构可在 `/api/openapi.json` 获取。为避免外部 CDN 和内联脚本，不启用 Swagger / ReDoc 页面。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/health` | 服务状态和版本 |
| GET | `/api/platforms` | 实际平台、方向、模块和限额 |
| POST | `/api/jobs` | multipart 上传并创建任务；成功 `202` |
| GET | `/api/jobs/{id}` | 查询单个任务 |
| GET | `/api/jobs/{id}/artifacts/theme` | 下载已验证主题 |
| GET | `/api/jobs/{id}/artifacts/report` | 下载映射报告 |
| DELETE | `/api/jobs/{id}` | 清理终态任务；成功 `204` |

所有修改请求必须携带：

```http
X-ThemeShift-Request: 1
```

服务器校验 Host、Origin 和浏览器 Fetch-Site，不开放跨域 CORS。脚本客户端可不带 Origin，但同样需要上述请求头；它是跨站请求防护，不是身份认证。

### 创建任务

`multipart/form-data`：只允许 1 个名为 `file` 的文件，不允许重复字段。

| 字段 | 必需 | 说明 |
| --- | --- | --- |
| `file` | 是 | 源主题文件 |
| `target` | 是 | 目标平台 ID，例如 `honor` |
| `source` | 否 | 源平台 ID；默认 `auto` |
| `dry_run` | 否 | `true` / `false`，默认 `false` |
| `name` / `name_en` | 否 | 中英文名称 |
| `author` / `version_number` | 否 | 作者与版本 |
| `brief` / `brief_en` | 否 | 中英文简介 |
| `icon_margin` | 否 | 图标内容区边距（整数像素）。仅当目标平台声明时可调；超出该平台范围返回 `400`，省略则使用平台默认值 |

示例（Windows PowerShell 使用 `curl.exe`）：

```bash
curl -H "X-ThemeShift-Request: 1" -F "file=@theme.mtz" -F "target=honor" http://127.0.0.1:8765/api/jobs
curl http://127.0.0.1:8765/api/jobs/<returned-id>
```

当前 Web 没有提供 CLI 的 Excel 包名清单、任意输出路径或自定义模块选择，避免增加上传面和路径权限。可通过 CLI / Python API 使用相应能力。

### 状态与错误

任务状态为 `queued`、`running`、`succeeded` 或 `failed`，快照包含：

- `id`、`filename`、`source`、`target`、`dry_run`
- `progress`、`stage`、`created_at`、`expires_at`（UTC ISO 时间，活动任务到期时间为空）
- `result`：元数据、静态 / 动态数、`stats`、警告、校验摘要和产物下载 URL
- `error`：失败时的 `{code, message}`

`stats.skipped` 表示未找到源图标的目标映射项，不代表所有未迁移文件的总数。成功只分析时 `validation=null`、`artifacts={}`。

同步 HTTP 错误统一为：

```json
{"error":{"code":"upload_too_large","message":"主题文件超过上传大小限制。"}}
```

常见状态：`400` 请求 / 来源错误，`403` 跨站拒绝，`404` 任务过期或产物不存在，`408` 上传超时，`409` 任务繁忙，`413` 超限，`415` 非 multipart，`422` 参数错误，`429` 容量不足。源主题损坏通常发生在接受任务之后，表现为任务 `failed`，不是把已成功的 POST 改成同步 HTTP 错误。

## 6. 安全预算与部署边界

默认预算：

| 对象 | 限制 |
| --- | --- |
| Web 单文件上传 | 128 MiB，边读边计量；请求另留 64 KiB 表单开销 |
| 上传总时间 | 120 秒；仅上传超时，**不是转换超时** |
| 表单 | 1 个文件、最多 10 个字段；字段 16 KiB，业务文本另有长度校验 |
| 每部分头部 | 16 KiB / 32 项；不完整 multipart、断线、取消和超限立即关闭已拥有的临时文件 |
| 每层 ZIP / 内嵌 ZIP 容器 | 512 MiB |
| 每层 ZIP 条目 | 20,000；含隐式目录的节点数另有限制 |
| 解压单文件 / 总量 | 64 MiB / 每层 1 GiB |
| 文本条目 / ZIP 中央目录 | 4 MiB / 16 MiB |
| 压缩比 | 大于 1 MiB 的高压缩条目限制 200:1 |
| 单张源图 | 16 × 1024 × 1024 像素，最长边 8192；解码前检查 |

只接受常见 Stored / Deflate ZIP；加密、分卷、某些非标准 ZIP64 结束记录或高风险编解码方式会被明确拒绝。拒绝路径穿越、绝对路径、Windows 设备 / 流名称、重复归一化路径、链接及文件 / 目录碰撞。限额由[归档工具](<../src/themeshift/packaging.py>)、[图像工具](<../src/themeshift/imaging.py>)和 [Web 设置](<../src/themeshift/web/settings.py>)实现。

这些不是整台机器的硬内存 / CPU / 磁盘配额：嵌套容器暂存和输出也占空间，很多大图仍可能耗时；当前工作线程无法被安全强杀，没有硬执行超时。服务目录应使用有足够空间的受控磁盘。默认防护适用于本机 / 可信使用，并不将 Python 插件变成沙箱，也不防同权限恶意本地进程竞争修改文件。

若确需局域网访问，需同时指定监听地址和允许的 Host。例如服务器实际内网地址为 `192.168.1.10` 时：

```bash
python -m themeshift serve --host 0.0.0.0 --allow-host 192.168.1.10
```

**不要直接部署到公网。** 对外服务至少需要具备身份认证、访问控制、HTTPS、请求限速与大小限制的反向代理，并进一步设计用户级任务隔离、持久化和进程级执行预算。当前不具备这些多租户能力。修改服务端 profile / 插件或依赖后需重启。

## 7. 开发验证

```bash
python -m pip install -e ".[web,dev,xlsx]"
python run_tests.py
ruff check src tests scripts
node --check src/themeshift/web/static/app.js
python -m build
```

仅核心安装时 Web 测试会跳过，因此验证 Web 必须安装上述 extras；CI 已安装 Web 依赖。

可选真实浏览器冒烟检查（无需把 Playwright 加入生产运行依赖）：

```bash
python -m pip install playwright
# 先在另一个终端启动 themeshift serve
python scripts/check_web_ui.py --browser msedge
# 非 Windows 可安装 Chromium，再选择 --browser chromium
python -m playwright install chromium
```

[浏览器检查脚本](<../scripts/check_web_ui.py>)覆盖 375 / 768 / 1024 / 1440 宽度、上传、真实转换、主题与报告下载、刷新恢复、清理、只分析及前端错误状态，截图放在 `.artifacts/`。如本机临时目录权限异常，可为测试进程配置项目内受控的 TEMP / TMP，不应因此放宽服务的上传安全检查。

## 8. 常见问题

**改了代码，但界面或产物没有变化。** 先确认服务加载的是哪份包：

```bash
python -c "import themeshift; print(themeshift.__file__, themeshift.__version__)"
```

如果路径位于 `site-packages` 而不是仓库的 `src/themeshift`，说明运行的是先前 `pip install .` 安装的旧拷贝。改用可编辑安装并重启服务：

```bash
python -m pip install -e ".[web]"
python -m themeshift serve
```

`serve` 启动后不会热加载 Python 模块；修改源码后必须重启进程。浏览器刷新只能更新前端静态文件，不能替换已加载的转换代码。

**任务成功但下载的包里没有预期修改。** 用任务编号确认产物时间，并检查包内条目属性：

```python
import zipfile
with zipfile.ZipFile("theme.hnt") as outer:
    print({(i.create_system, i.external_attr & 0xFF) for i in outer.infolist()})
```

当前版本应为 `{(0, 32)}`（FAT 来源、归档位 `0x20`）。若看到 `{(3, 0)}`，说明生成它的仍是 1.1.0 或更早的安装包，需要按上一节改为可编辑安装并重新转换。
