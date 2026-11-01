# 新增平台与模块

本指南面向适配器开发者。**当前内置的实际能力仍只有小米 `.mtz` 读取、荣耀 `.hnt` 写出，以及该方向的图标 / 登记动态资源迁移。** 协议允许扩展品牌和模块，不代表反向移植或全主题迁移已经可用。总体设计见[架构说明](<architecture.md>)。

以下 `demo`、`demo_plugin` 和 `.demo` 都是虚构示例，不是仓库已经提供的品牌、插件或主题格式。

## 1. 先确定角色、模块和方向

新增一种品牌格式可能需要：

- **读取器**：把该格式解读为 `SourceTheme`。
- **写出器**：把 `NormalizedTheme` 写成该格式并回读自检。
- **方向映射**：为每个实际支持的 source → target 设计独立映射。
- **模块转换器**：只有内置图标模块之外的新语义转换才需要额外实现并注入；声明模块名本身不会提供转换能力。

同一品牌可以只读、只写或同时具备两个独立角色。不必修改核心业务流程，也不必把读取器和写出器合并成一个类。

## 2. 准备完整的数据目录

[数据仓库](<../src/themeshift/repository.py>)依次选择：显式 `--data-dir` / `DataRepository.create(override)`、环境变量 `THEMESHIFT_DATA`、[内置数据目录](<../src/themeshift/data/>)。

**外部目录替换整个数据根，不会自动与内置数据合并。** 例如要做小米 → Demo，外部目录同时需要小米档案、Demo 档案和该方向的映射；可以复制[现有小米档案](<../src/themeshift/data/profiles/xiaomi.json>)作为已安装内置读取器的配置。

```text
my-platform-pack/
├── profiles/
│   ├── xiaomi.json
│   └── demo.json
└── mappings/
    └── xiaomi_to_demo.json
```

`profiles/` 是数据根的必需目录。`mappings/` 可以不存在，但没有对应方向文件就不能执行该方向的转换。

### 推荐的 schema 2 双角色档案

虚构的 `profiles/demo.json`：

```json
{
  "schema_version": 2,
  "name": "demo",
  "display_name": "Demo（示例平台）",
  "adapters": {
    "source": {
      "implementation": "demo_plugin:DemoReader",
      "modules": ["icons", "dynamic_icons"]
    },
    "target": {
      "implementation": "demo_plugin:DemoWriter",
      "modules": ["icons", "dynamic_icons"]
    }
  },
  "input_extensions": [".demo"],
  "output_extension": ".demo",
  "output_suffix": "_demo",
  "report_suffix": "_mapping"
}
```

这只是声明示例：只有两种模块都实际实现后才能保留上述能力；如果只实现静态图标，应从对应角色删除 `dynamic_icons`。只读 / 只写平台直接省略另一个角色，不能填 `null`。

[档案校验](<../src/themeshift/profiles.py>)与[注册器](<../src/themeshift/registry.py>)的规则：

- `name` 与档案文件名一致；平台 / 模块 ID 为小写字母开头，后接小写字母、数字、`_` 或 `-`，总长不超过 64。
- 每个角色的 `modules` 是非空、无重复的有效 ID 数组；源角色要求 `input_extensions`，目标角色要求 `output_extension`。
- 扩展名为点号加 1～16 个英文字母或数字，是单个后缀，不是复合扩展名；输出 / 报告后缀不能包含路径分隔符等危险字符。
- `implementation` 必须为可导入的 `module:Class`。类接收**完整的 profile 字典**，读取类需有可调用的 `load()`，写出类需有 `build()`。
- 布局、画布和元数据字段约定等平台专用配置可放在同一个 profile，由对应适配器验证并解释；不要把荣耀的特有字段当成通用必填项。

实现类须已安装或位于 `PYTHONPATH`。档案和 Python 插件都应来自受信任的本地配置；`module:Class` 是代码执行入口，不应从上传主题中自动加载。

### 旧 schema 1 仍兼容

例如旧的单角色档案：

```json
{
  "schema_version": 1,
  "name": "demo",
  "display_name": "Demo（旧格式示例）",
  "kind": "source",
  "implementation": "demo_plugin:DemoReader",
  "input_extensions": [".demo"]
}
```

省略 `schema_version` 也按 v1 处理。v1 的能力按旧约定隐式设为 `icons`、`dynamic_icons`，但不会产生另一个角色。需要双角色、准确收窄能力或声明新模块时使用 v2；不要混用 v1 的顶层 `kind` / `implementation` 与 v2 的 `adapters`。

## 3. 为每个方向设计映射

虚构的 `mappings/xiaomi_to_demo.json`：

```json
{
  "source": "xiaomi",
  "target": "demo",
  "source_system": {
    "prefixes": ["com.miui.", "com.xiaomi."],
    "exact": ["com.android.calendar"]
  },
  "system_app_map": {
    "com.demo.calendar": ["com.xiaomi.calendar", "com.android.calendar"]
  },
  "dynamic_assets": [
    {
      "source": "com.android.calendar/icon_bg.png",
      "target": "com.demo.calendar/calendar_base.png"
    }
  ]
}
```

[映射实现](<../src/themeshift/mapping.py>)的语义是：

- `system_app_map` 的键是目标包名，值是有序的源候选列表，取第一个存在的源图标。`stats.skipped` 统计没有匹配源的目标映射项，不等于所有被排除文件的总数。
- `source_system` 区分源系统应用与第三方应用；无显式目标映射的源系统图标不会作为第三方直通，安全的第三方包名可原样保留。
- `dynamic_assets` 使用 `<package>/<filename>` 引用，只有协商启用 `dynamic_icons` 时才按登记项转换；不推测动态行为或缺失角色。
- 文件名、请求方向与 JSON 的 `source` / `target` 必须一致，即使是只处理非图标模块的扩展，也仍需提供显式方向文件。

示例映射并不完整，不能替代对真实厂商应用及资源语义的调查。实际内置映射可参考[小米 → 荣耀数据](<../src/themeshift/data/mappings/xiaomi_to_honor.json>)。

**反向必须另行设计、实现和测试，不能自动倒置。** 想做 Demo → 小米还需要小米写出器及独立的反向映射；当前没有小米写出器，不能只加一份反向 JSON 就声称支持。即使 source 与 target 是同一个品牌，也需要明确的同品牌方向映射。

## 4. 实现独立的读取器和写出器

协议定义见 [base.py](<../src/themeshift/platforms/base.py>)。以下仅为接口骨架，不是可直接转换主题的 Demo 实现：

```python
from pathlib import Path
from themeshift.models import BuildReport, NormalizedTheme, SourceTheme


class DemoReader:
    def __init__(self, profile: dict) -> None:
        self.profile = profile
        self.name = profile["name"]

    def load(self, package_path: Path, work_dir: Path) -> SourceTheme:
        raise NotImplementedError("实现 Demo 格式读取、验证和 IR 构造")


class DemoWriter:
    def __init__(self, profile: dict) -> None:
        self.profile = profile
        self.name = profile["name"]

    def build(
        self, theme: NormalizedTheme, out_path: Path, work_dir: Path
    ) -> BuildReport:
        raise NotImplementedError("实现 Demo 格式构建及回读自检")
```

### 读取器要点

1. 验证真实容器、目录约定和资源内容，不能只信文件扩展名；将解包或派生文件写入提供的工作目录。
2. 返回[中间模型](<../src/themeshift/models.py>) `SourceTheme(schema_version=1)`：`ThemeMeta`、`StaticIcon`、`DynamicAsset` 及必要的来源信息。这里的 IR 版本 1 与 profile schema 2 无关。
3. 新模块资源放入 `modules[module_id]` 的 `ThemeResource` 列表；明确 `key`、媒体类型和属性的含义，不让另一厂商的写出器自行猜源目录。
4. 元数据短字段最大 200 字符，简介最大 2000 字符，不能含 XML 禁止的控制字符；服务层也会检查源元数据与覆盖值。
5. 对异常源格式抛出 `SourceFormatError`，对错误平台配置抛出 `ConfigError`。不要清空调用方提供的工作父目录或修改原始主题。

归档格式可以复用 [packaging.py](<../src/themeshift/packaging.py>) 的 `safe_extract` / `ArchiveLimits`；图像处理可复用 [imaging.py](<../src/themeshift/imaging.py>)。新适配器仍需为自己的嵌套容器和资源解析设置合理预算，不能绕过安全验证直接批量解压。

### 写出器与通用自检要点

1. 消费已映射的 `NormalizedTheme(schema_version=1)`，仅处理自己实际支持的模块。图标内容区、预览和描述规则由目标格式决定。
2. 输出到传入的 `out_path`，把阶段文件放在提供的工作区。该路径是服务的**暂存路径**，写出器不负责覆盖用户最终输出。
3. 回读实际产物并生成 `BuildReport`；新平台使用非空的 `checks` 和必要的 `issues`，不要伪填荣耀专有字段，也不要无条件返回通过。

```python
# format_ok、resources_ok、issues 必须来自对实际产物的检查。
report = BuildReport(
    output=out_path,
    checks={"demo_format": format_ok, "resource_manifest": resources_ok},
    issues=issues,
)
```

`checks` 每项必须严格为 `True`，`issues` 非空即失败；空 `checks` 才回落到旧荣耀检查逻辑。服务还会确认暂存文件确实存在。自检失败抛出 `BuildValidationError`（CLI 退出码 `5`），**新产物不会发布**。

可组合 [packaging.py](<../src/themeshift/packaging.py>) 的 `write_zip` / `write_text_lf` 和 [naming.py](<../src/themeshift/naming.py>) 的 `is_safe_component`。`write_zip` 固定时间戳与权限，但保持调用方给出的条目顺序；适配器应稳定排序。最终由服务校验后逐文件原子发布主题和映射报告，不是两个文件的整体事务。

### 可选渲染选项（图标边距示例）

目标平台若希望暴露“图标边距”，按以下方式选择加入；不声明 `options` 的写出器完全不受影响：

```json
{
  "icon": {"canvas": 192, "content": 172}
}
```

```python
class DemoWriter:
    def __init__(self, profile: dict) -> None:
        self.canvas = int(profile["icon"]["canvas"])
        self.content = int(profile["icon"]["content"])

    def build(self, theme, out_path, work_dir, options=None) -> BuildReport:
        margin = None if options is None else options.get("icon_margin")
        content = self.content if margin is None else self.canvas - 2 * int(margin)
        # 用 content 归一化图标；再回读校验没有图标越出内容区
        ...
```

规则：

- 服务只有在档案声明自洽的 `icon.canvas` / `icon.content` **且**写出器接受 `options` 时，才在 `/api/platforms` 中返回 `icon_margin`（`min`、`max`、`default`、`canvas`），前端据此显示滑块；CLI 对应 `--icon-margin`。
- 服务在创建工作目录前校验整数与范围（至少保留 `max(32, canvas/4)` 的内容区）；写出器仍应自行校验，不能假设调用方可信。
- 未知选项名应安全忽略，不能凭选项执行任意路径或代码；选项只影响绘制，不改变包结构与 IR。
- 自检可以像荣耀写出器一样记录 `content_size` 与 `icons_outside_content`，越界即失败，避免“调小边距后图标被系统裁切”却仍通过自检。

## 5. 新模块必须有真正的转换实现

当前内置转换仅有 `icons` / `dynamic_icons`。增加壁纸、锁屏或 `system_ui` 时，需要同时完成：

1. 源适配器真实读取该模块，目标适配器真实写出并验证该模块，双方在 schema 2 中声明能力。
2. 定义模块的资源键、属性、兼容策略及有损转换规则。
3. 实现 `ModuleTransformer.transform(source, target, mapping) -> None`，显式填充 `target.modules`，必要时通过 `target.note()` 和 `target.warnings` 记录处理结果。
4. 将转换器注入共享服务，并保留该方向的显式映射文件。

安装了自行实现的插件后，装配方式如下；**仓库没有这个 Demo 壁纸实现**：

```python
from pathlib import Path
from demo_plugin import DemoWallpaperTransformer
from themeshift.repository import DataRepository
from themeshift.service import MigrationService

service = MigrationService(
    DataRepository.create(Path("my-platform-pack")),
    transformers={"wallpapers": DemoWallpaperTransformer()},
)
```

转换器名称须为有效模块 ID，不能覆盖 `icons` 或 `dynamic_icons`。只在双方能力及服务支持的交集中出现的模块才可协商；未知模块资源不会自动复制。通过 Python 的 `MigrationRequest.modules` 可以进一步选择模块，必须是非空、无重复的可用子集。

默认 CLI 的 `--modules` 只有选择作用，不会动态加载转换器；自定义模块需要自己的装配入口。Web 可以通过 `create_app(service=service)` 复用已注入的服务，不需要把 HTTP 或任务队列依赖加进适配器。Web 的本机边界、安全预算和运行方式见 [Web 工作台说明](<web.md>)。

完整的规范化语义映射层尚未实现。可以先定义跨品牌应用 / 资源角色，再逐模块实现转换和目标自检，最后扩展品牌、方向与兼容矩阵；这些是后续规划，不是当前已经可用的全主题转换功能。

## 6. 接入验证与回归测试

完成插件和数据后，从仓库根目录或已安装的环境检查实际能力：

```bash
themeshift platforms --data-dir ./my-platform-pack
themeshift migrate 主题.mtz --from xiaomi --to demo --data-dir ./my-platform-pack --dry-run
themeshift migrate 主题.mtz --from xiaomi --to demo --data-dir ./my-platform-pack
```

`dry_run` 只做读取、映射、协商模块的转换和统计，不构建包、不输出报告文件，也不执行目标图像归一化或输出自检，不能替代正常转换测试。多个读取器共用扩展名时必须显式指定 `--from`。

每次转换调用使用独占工作子目录。直接使用 `MigrationRequest(work_dir=parent)` 只是指定父目录，`keep_work` 决定保留；CLI 的 `--workdir parent` 则会保留新建子目录供调试。不要让适配器删除父目录或其他任务文件。

现有测试可以作为实现范例：

| 测试 | 可参考的内容 |
| --- | --- |
| [test_profiles.py](<../tests/test_profiles.py>) | v1 兼容、v2 双角色、模块 / ID 校验、独立实现类、反向不推断 |
| [test_service.py](<../tests/test_service.py>) | `ToyReader` / `ToyWriter`、通用 `BuildReport`、显式转换器、双方向独立数据、IR 版本、只分析、并发工作隔离、失败不发布 |
| [test_mapping.py](<../tests/test_mapping.py>) | 包名候选、源系统分类和动态资源映射 |
| [test_packaging.py](<../tests/test_packaging.py>)、[test_imaging.py](<../tests/test_imaging.py>) | 恶意 / 损坏输入、预算与图像处理边界 |
| [test_end_to_end.py](<../tests/test_end_to_end.py>) | 当前小米 → 荣耀的真实流水线与输出自检 |

服务测试中的 `WallpaperTransformer` 只转换合成文本资源，证明通用扩展点有效，**不是已经实现的真实壁纸迁移器**。

新增适配器至少覆盖：角色与模块缺失、错误方向、来源歧义、未知 IR 版本、损坏源、目标回读失败、缺失产物、只分析无下载产物、并行同名输入互不删工作区、失败后既有输出不变。对每个真实方向和模块，另外建立目标格式可验证的端到端用例。

```bash
python -m unittest tests.test_profiles tests.test_service
python run_tests.py
```

只有实现、方向数据和验证均完成后，才将对应能力加入对外声明；不能把示例 profile 或一次 toy 测试当作新增真实品牌支持的证据。
