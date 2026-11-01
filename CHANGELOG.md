# Changelog

All notable changes to this project are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Deterministic archive output (stable timestamps and ordering).
- External data directory override via `--data-dir` / `THEMESHIFT_DATA`.
- Unit and end-to-end test suite built on the standard library.

## [1.2.0] - 2026-11-01

### Added

- Adjustable icon margin: Web slider and `--icon-margin`, with capability-driven bounds, server-side validation and a read-back safe-area check.
- Faster icon generation: parallel icon rendering (overridable via the HONOR profile's `icon.workers`), PNG encoding without the slow `optimize` filter search, streamed ZIP entries and a cheaper read-back verification.

### Changed

- Split the Web workspace into grouped steps (文件与方向 → 图标设置 → 任务结果) and moved platform capabilities into a dialog, so each screen stays focused instead of stacking every panel on one page.
- Restyled the Web UI in an iOS-like flat language: `#f2f2f7` grouped background, white cards with hairline separators, system typography, a single blue accent and no gradients, glass blur or layered shadows; paddings and type sizes were tightened to fit more information per screen.

### Fixed

- Generated `.hnt` entries now record Windows/DOS file attributes (`create_system = 0`, attribute byte `0x20`); self-verification rejects archives without them. Packages built by 1.1.0 still need to be regenerated.
- Folders are now written as explicit ZIP directory entries with the DOS directory attribute (`0x10`), ordered before their contents, instead of relying on implicit paths.
- Fixed jagged icon curves: the canvas no longer uses the icon as a paste mask (which squared alpha and collapsed antialiasing); large reductions now use area averaging instead of ringing Lanczos.
- HONOR browser mapping now accepts both Xiaomi browser packages (`com.android.browser`, `com.miui.browser`) for the target `com.honor.browser`.
- Removed the incorrect Mi Home → HONOR MagicHome (`com.hihonor.magichome`) alias.

## [1.1.0] - 2026-11-01

### Added

- Optional local Web workspace: FastAPI/Uvicorn API and zero-build HTML/CSS/JavaScript UI.
- Upload, capability-driven platform choices, metadata overrides, dry-run analysis, task progress, downloads and cleanup.
- Bounded in-process conversion queue with private task directories, result expiry and download leases.
- Schema v2 independent reader/writer roles per vendor; schema v1 remains compatible.
- Versioned neutral IR resource modules and injectable module transformers for future non-icon migration.
- Generic named build checks, API integration tests, multipart lifecycle tests and an optional real-browser smoke script.

### Changed

- CLI and Web share the transport-independent MigrationService.
- Output validation failures return exit code 5 and do not publish a new artifact.
- Explicit work directories now contain isolated per-run subdirectories, avoiding concurrent cleanup collisions.
- Xiaomi metadata preserves optional version, English title and descriptions.

### Security

- Bounded archive/image decoding, portable path/alias checks, ZIP-bomb limits and damaged-input error handling.
- Same-origin request checks, upload quotas, immediate multipart spool cleanup on errors/cancellation, and safe artifact-only downloads.

Only Xiaomi → HONOR icons are currently implemented; reverse directions, other vendors and full-theme modules are extension points, not shipped conversions.

## [1.0.0] - 2026-11-01

### Added

- One-click migration from Xiaomi `.mtz` icon themes to HONOR `.hnt` icon themes.
- HONOR icon canvas normalization: 192x192 canvas, 172x172 content area.
- HONOR preview rendering: `icon_small.jpg` (510x345, 3x2),
  `icon_small_1.jpg` (640x640, 2x2), `preview_icons_0/1.jpg` (1080x2160).
- `description.xml` writer with UTF-8 (no BOM), Unix line endings and
  `extraContentFlag = "5"`.
- Data-driven system package mapping, third-party package passthrough and
  optional target package allow-list (`--packages`).
- Extensible platform layer: new platforms are added through JSON profiles,
  JSON mappings and a `module:Class` implementation reference.
