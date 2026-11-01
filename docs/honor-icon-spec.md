# 荣耀图标主题规范

本文记录 `themeshift` 写出 `.hnt` 时遵循的约定，对应 `data/profiles/honor.json`。

## 图标

| 项目 | 值 |
| --- | --- |
| 画布 | `192×192` |
| 内容区 | 默认 `172×172`，可用 `--icon-margin` / Web“图标边距”调整 |
| 安全边距 | 默认四边 `10 px`；可调范围 `0–72 px`，至少保留 `48×48` 内容区 |
| 格式 | PNG（RGBA） |
| 缩放 | 等比缩小，不放大；中等倍率用 Lanczos，缩小超过 3.5 倍改用面积平均 |
| 合成 | source-over 叠加，保留原始抗锯齿边缘 |

处理方式：先等比缩放到不超过内容区，再居中叠加到透明的 `192×192` 画布。
默认边距下满幅图标的 alpha 包围盒为 `(10, 10, 182, 182)`；边距为 `m` 时，
内容区为 `(192 − 2m)²`，包围盒相应内缩到 `m`。写出器回读时校验每个图标都在
内容区内，越界会使自检失败、不发布新包。

缩放与合成会直接影响曲线质量：大幅缩小时使用面积平均（`BOX`）而不是单次
Lanczos，避免振铃与混叠；叠加使用 `alpha_composite` 而不是以图标自身作蒙版
`paste`，后者会把 alpha 再乘一次（50% 覆盖变成 25%），使抗锯齿过渡塌缩成硬
阶梯，曲线出现明显锯齿。

## 预览图

| 文件名 | 尺寸 | 排布 | 用途 |
| --- | --- | --- | --- |
| `icon_small.jpg` | `510×345` | 3 列 × 2 行 | 资源列表、混搭页 |
| `icon_small_1.jpg` | `640×640` | 2 列 × 2 行 | 资源列表、混搭页 |
| `preview_icons_0.jpg` | `1080×2160` | 4 × 8 | 资源详情页 |
| `preview_icons_1.jpg` | `1080×2160` | 4 × 8（从第 32 个图标开始） | 资源详情页 |

预览背景取 `preview_background`（默认 `#F6F7FB`），只使用静态图标，按包名排序后取用。

## description.xml

```xml
<?xml version="1.0" encoding="utf-8"?>
<HnTheme>
  <title>超拟圆图标</title>
  <designer>Lilfox（图标原作）</designer>
  <version>1.0.0</version>
  <briefInfo>超拟圆荣耀图标主题。</briefInfo>
  <extra>
{
  "extraContentFlag": "5",
  "zh": {
    "title": "超拟圆图标",
    "briefInfo": "超拟圆荣耀图标主题。"
  },
  "en": {
    "title": "Super Rounded Icons",
    "briefInfo": "Icon theme for HONOR."
  }
}
  </extra>
</HnTheme>
```

约束：

- UTF-8 编码，**不带 BOM**
- 仅使用 Unix 换行 `LF`
- `extraContentFlag = "5"` 表示单独上架的图标资源
- 文本节点做 XML 转义

## 包结构

```text
theme.hnt
├── description.xml
├── icons                       # 内层 zip（无扩展名）
│   ├── <package>.png
│   └── dynamic_icons/
│       └── <package>/<file>.png
└── preview/*.jpg
```

ZIP 条目按 Windows/DOS 习惯写入：`create_system = 0`（FAT 来源）；普通文件的外置属性
低字节为 `0x20`（归档位），**文件夹为 `0x10`（目录位）**。文件夹会写成显式条目
（`preview/`、`dynamic_icons/`、`dynamic_icons/<package>/`），并排在所属文件之前；
不能只依赖路径隐式推断。部分荣耀主题软件读取文件夹条目及其 DOS 属性，缺失时可能
无法解析目录结构。时间戳统一为 `1980-01-01`，条目顺序稳定。

写出器回读自检会统计 `entries_without_dos_attributes`：文件必须为 `0x20`、文件夹必须
为 `0x10`，不为 0 即判定失败。

图标渲染默认按 CPU 核数多线程进行（上限 8），条目顺序仍由包名排序决定，因此输出
保持确定性；可在档案的 `"icon": {"workers": 4}` 中限制线程数，避免与 Web 多任务
叠加时过度占用 CPU。PNG 以压缩级别 6 写出，不做逐图标 `optimize` 搜索：编码快约
一倍，包体约大 2–4%。

## 系统应用映射

- 目标包名来自 `data/mappings/xiaomi_to_honor.json` 的 `system_app_map`。
- 第三方应用保留原包名，不会被改名。
- 可通过 `--packages` 传入 `.xlsx` 或 `.json` 白名单，只输出列表内的系统应用。
- 图标边距不影响包名映射，只改变图标内容区大小。

示例 `examples/package-list.json`：

```json
["com.honor.browser", "com.hihonor.calculator", "com.hihonor.contacts"]
```

## 自检清单

`BuildReport` 在打包后回读输出并检查：

- [x] 所有静态图标为 `192×192`
- [x] 预览图数量与尺寸完全匹配
- [x] `description.xml` 无 `CRLF`
- [x] `description.xml` 无 BOM
- [x] `extraContentFlag` 可解析
