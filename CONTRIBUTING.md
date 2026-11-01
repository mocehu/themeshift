# 贡献指南

感谢你愿意参与。这个项目最需要两类贡献：**新的包名映射**和**新的平台实现**。

## 开发环境

```bash
git clone <repo>
cd themeshift
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -e ".[dev,xlsx,web]"
```

运行测试与静态检查：

```bash
python run_tests.py
ruff check src tests scripts
```

`run_tests.py` 在任意工作目录都能运行；也可以使用
`python -m unittest discover -s tests -t .`。

## 最常见的贡献：补一条包名映射

编辑 `src/themeshift/data/mappings/xiaomi_to_honor.json`：

```json
{
  "system_app_map": {
    "com.hihonor.新应用": ["com.miui.旧应用", "com.android.旧应用"]
  }
}
```

规则：

- 键是**目标平台**的包名，值是**候选源包名**数组，按顺序取第一个存在的。
- 候选里不要写 `.png` 后缀。
- 第三方应用不需要登记，它们会原包名直通。
- 源平台系统包若不在候选里，会被丢弃，不会污染输出。

提交前请附上证据（应用信息页截图、`pm list packages` 输出，或官方文档链接）。

## 新增平台

参考 [docs/adding-a-platform.md](docs/adding-a-platform.md)。要点：

1. 数据：`profiles/<name>.json` 与 `mappings/<source>_to_<target>.json`。
2. 代码：实现 `SourcePlatform` 或 `TargetPlatform` 协议。
3. 测试：至少覆盖「读取/写出 + 一次端到端」。

平台实现里**不要硬编码**包名、尺寸或文件名，这些属于数据层。

## 代码风格

- Python 3.10+，全部使用类型注解。
- 行宽 100，遵循 `ruff` 配置（E/F/I/UP/B/SIM）。
- 公开函数写简短 docstring，解释「为什么」而非「做了什么」。
- 新增行为请补测试；测试不依赖真实主题素材，用 `tests/support.py` 合成。

## 提交信息

使用 Conventional Commits：

```
feat(mapping): add Honor browser package
fix(honor): keep 10px margin for small icons
docs: document preview image sizes
```

## Pull Request 清单

- [ ] `python run_tests.py` 通过
- [ ] `ruff check src tests scripts` 通过
- [ ] 新增/修改的 JSON 通过校验（`themeshift platforms` 能正常列出）
- [ ] 没有提交任何 `.mtz`、`.hnt` 或主题美术素材
- [ ] 变更已写入 `CHANGELOG.md` 的 Unreleased 段

## 素材与版权

请勿在仓库、issue 或 PR 中上传受版权保护的主题包。复现问题时请提供结构描述、
最小化的合成样例，或仅在本地验证。
