"""Bounded archive helpers, portable path safety and reproducible output.

Extraction destinations must be private to the caller (no concurrent filesystem
writers). Names and resource budgets are checked before writing any members;
individual files are streamed to temporary siblings and replaced only after CRC
validation. Extraction is not a transaction: already completed files can remain
if a later member is corrupt, so web jobs should use disposable work directories.
"""

from __future__ import annotations

import io
import os
import shutil
import stat
import struct
import tempfile
import unicodedata
import zipfile
import zlib
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from .errors import SourceFormatError

FIXED_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
# Windows/DOS-created archives mark regular files with the archive bit and
# record the entry as FAT-created. Several vendor installers read that byte and
# refuse entries whose DOS attribute is missing, so output must not look like a
# Unix-created ZIP even though we run on any platform.
DOS_ARCHIVE_ATTRIBUTE = 0x20
DOS_DIRECTORY_ATTRIBUTE = 0x10


def has_dos_attributes(info: zipfile.ZipInfo) -> bool:
    """Whether an entry records a Windows/DOS attribute byte and FAT origin.

    Vendor theme installers commonly read the low byte of the external
    attributes; ``create_system`` must also be ``0`` so the archive does not look
    like a Unix ZIP with a meaningless attribute byte.
    """

    if info.create_system != 0:
        return False
    expected = DOS_DIRECTORY_ATTRIBUTE if info.is_dir() else DOS_ARCHIVE_ATTRIBUTE
    return info.external_attr & 0xFF == expected
TEXT_ENCODINGS = ("utf-8-sig", "utf-16", "gb18030", "latin-1")
_MIB = 1024 * 1024
_COPY_CHUNK = 64 * 1024
_RATIO_GRACE_BYTES = _MIB
_MAX_PATH_DEPTH = 32
_MAX_PATH_LENGTH = 1024
_WINDOWS_DEVICES = {
    "con", "prn", "aux", "nul", "clock$", "conin$", "conout$",
    *(f"{prefix}{digit}" for prefix in ("com", "lpt") for digit in "123456789¹²³"),
}
_ARCHIVE_ERRORS = (
    zipfile.BadZipFile, zipfile.LargeZipFile, OSError, EOFError,
    RuntimeError, ValueError, NotImplementedError, zlib.error, struct.error,
)


@dataclass(frozen=True)
class ArchiveLimits:
    """Per-archive budgets, generous enough for thousands of normal PNG icons.

    ``max_archive_bytes`` also bounds a nested ZIP container; extracted members
    use the smaller ``max_member_bytes`` budget. Nested containers are spooled
    to disk, not retained as a potentially hundreds-of-MiB bytes object. Only
    stored/deflated ZIPs are accepted: LZMA and similar codecs can allocate large
    decoder dictionaries independently of their claimed uncompressed size.
    """

    max_members: int = 20_000
    max_archive_bytes: int = 512 * _MIB
    max_member_bytes: int = 64 * _MIB
    max_total_bytes: int = 1024 * _MIB
    max_text_bytes: int = 4 * _MIB
    max_directory_bytes: int = 16 * _MIB
    max_compression_ratio: int = 200

    def __post_init__(self) -> None:
        for name, value in vars(self).items():
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")


DEFAULT_ARCHIVE_LIMITS = ArchiveLimits()


def _format_error(detail: str) -> SourceFormatError:
    return SourceFormatError(f"主题压缩包不安全或已损坏：{detail}，已中止读取/解压。")


def _check_container(stream: BinaryIO, limits: ArchiveLimits) -> None:
    """Bound the central directory *before* ZipFile allocates its ZipInfo list.

    Count actual central-directory records as well as checking the EOCD count:
    attackers can understate the latter. ZIP64 end records with no extensible
    data are supported (including ZIP64 local headers on small normal ZIPs).
    """

    position = stream.tell()
    try:
        stream.seek(0, os.SEEK_END)
        size = stream.tell()
        if size > limits.max_archive_bytes:
            raise _format_error("压缩包大小超过限制")
        tail_size = min(size, 65535 + 22)
        stream.seek(size - tail_size)
        tail = stream.read(tail_size)
        offset = tail.rfind(b"PK\x05\x06")
        if offset < 0 or len(tail) - offset < 22:
            raise _format_error("缺少有效的 ZIP 结束记录")
        record = struct.unpack_from("<4s4H2LH", tail, offset)
        _, disk, directory_disk, disk_count, count, directory_size, directory_offset, comment = record
        if offset + 22 + comment != len(tail):
            raise _format_error("ZIP 结束记录或注释长度无效")
        end = size - tail_size + offset
        directory_end = end
        locator = b""
        if end >= 20:
            stream.seek(end - 20)
            locator = stream.read(20)
        if locator.startswith(b"PK\x06\x07"):
            _, zip64_disk, _, disks = struct.unpack("<4sLQL", locator)
            if zip64_disk != 0 or disks != 1 or end < 76:
                raise _format_error("不支持分卷 ZIP")
            directory_end = end - 76
            stream.seek(directory_end)
            zip64 = stream.read(56)
            if len(zip64) != 56 or not zip64.startswith(b"PK\x06\x06"):
                raise _format_error("ZIP64 结束记录无效")
            record64 = struct.unpack("<4sQ2H2L4Q", zip64)
            _, record_size, _, _, disk, directory_disk, disk_count, count, directory_size, directory_offset = record64
            if record_size != 44:
                raise _format_error("不支持带扩展结束记录的 ZIP64")
        if disk != 0 or directory_disk != 0 or disk_count != count:
            raise _format_error("不支持分卷 ZIP")
        if count > limits.max_members:
            raise _format_error("压缩包条目数量超过限制")
        if directory_size > limits.max_directory_bytes:
            raise _format_error("ZIP 中央目录大小超过限制")
        start = directory_end - directory_size
        if start < 0 or directory_offset > start:
            raise _format_error("ZIP 中央目录位置无效")
        stream.seek(start)
        directory = stream.read(directory_size)
        if len(directory) != directory_size:
            raise _format_error("ZIP 中央目录不完整")
        actual_count = 0
        cursor = 0
        while cursor < len(directory):
            if actual_count >= limits.max_members:
                raise _format_error("压缩包实际条目数量超过限制")
            if len(directory) - cursor < 46 or directory[cursor:cursor + 4] != b"PK\x01\x02":
                raise _format_error("ZIP 中央目录记录无效")
            name_size, extra_size, comment_size = struct.unpack_from("<3H", directory, cursor + 28)
            cursor += 46 + name_size + extra_size + comment_size
            if cursor > len(directory):
                raise _format_error("ZIP 中央目录记录不完整")
            actual_count += 1
        if actual_count != count:
            raise _format_error("ZIP 中央目录条目数量不一致")
    finally:
        stream.seek(position)


def _member_path(member: zipfile.ZipInfo) -> tuple[tuple[str, ...], bool]:
    original = getattr(member, "orig_filename", member.filename)
    name = original.replace("\\", "/")
    if not name or name.startswith("/") or any(ord(char) < 32 for char in name):
        raise _format_error(f"非法路径 {original!r}")
    is_dir = name.endswith("/")
    parts = tuple((name[:-1] if is_dir else name).split("/"))
    if len(parts) > _MAX_PATH_DEPTH or len(name) > _MAX_PATH_LENGTH:
        raise _format_error(f"路径过长 {original!r}")
    for part in parts:
        stem = part.split(".", 1)[0]
        device = stem.rstrip(" ").casefold()
        # A generated DOS 8.3 alias can refer to a *different* earlier member
        # once it exists (LONGFI~1.PNG -> LongFilename.png on some NTFS volumes).
        # Reject this ambiguous spelling rather than depending on host settings.
        short_alias = "~" in stem and stem.rsplit("~", 1)[1].isdigit()
        if (
            part in ("", ".", "..")
            or len(part) > 255
            or part.endswith((".", " "))
            or any(char in '<>:"|?*' for char in part)
            or device in _WINDOWS_DEVICES
            or short_alias
        ):
            raise _format_error(f"非法或不兼容 Windows 的路径 {original!r}")
    mode = stat.S_IFMT(member.external_attr >> 16)
    if mode not in (0, stat.S_IFREG, stat.S_IFDIR) or member.external_attr & 0x400:
        raise _format_error(f"不允许符号链接或特殊文件 {original!r}")
    if mode == stat.S_IFDIR and not is_dir:
        raise _format_error(f"目录路径缺少末尾分隔符 {original!r}")
    if is_dir and member.file_size != 0:
        raise _format_error(f"目录条目包含数据 {original!r}")
    if member.flag_bits & 1:
        raise _format_error(f"不支持加密条目 {original!r}")
    if member.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
        raise _format_error(f"不支持的 ZIP 压缩算法 {member.compress_type}")
    return parts, is_dir


def _check_member_size(member: zipfile.ZipInfo, maximum: int, limits: ArchiveLimits) -> None:
    if member.file_size < 0 or member.compress_size < 0 or member.file_size > maximum:
        raise _format_error(f"条目大小超过限制 {member.filename!r}")
    if member.file_size > max(_RATIO_GRACE_BYTES, member.compress_size * limits.max_compression_ratio):
        raise _format_error(f"压缩比超过限制 {member.filename!r}")


def _guard_members(
    members: list[zipfile.ZipInfo],
    *,
    limits: ArchiveLimits,
    member_limit: int,
) -> list[tuple[zipfile.ZipInfo, tuple[str, ...], bool]]:
    if len(members) > limits.max_members:
        raise _format_error("压缩包条目数量超过限制")
    plan = []
    # Include implicit parent directories to catch file/directory conflicts in
    # either order and aliases such as Res/a.png versus res/b.png.
    paths: dict[tuple[str, ...], tuple[tuple[str, ...], bool]] = {}
    explicit: set[tuple[str, ...]] = set()
    total = 0
    for member in members:
        parts, is_dir = _member_path(member)
        _check_member_size(member, member_limit, limits)
        total += member.file_size
        if total > limits.max_total_bytes:
            raise _format_error("解压总大小超过限制")
        # upper() additionally catches NTFS aliases such as dotless ı / I;
        # casefold() and NFC conservatively protect other host filesystems.
        key = tuple(unicodedata.normalize("NFC", part).upper().casefold() for part in parts)
        for depth in range(1, len(parts) + 1):
            parent_key, spelling = key[:depth], parts[:depth]
            directory = depth < len(parts) or is_dir
            existing = paths.get(parent_key)
            if existing is None and len(paths) >= 2 * limits.max_members:
                raise _format_error("解压路径数量超过限制（包括隐式目录）")
            if existing is not None and (
                existing != (spelling, True) or not directory
                or (depth == len(parts) and parent_key in explicit)
            ):
                raise _format_error(f"重复、归一化冲突或文件/目录冲突 {member.filename!r}")
            paths[parent_key] = (spelling, directory)
        explicit.add(key)
        plan.append((member, parts, is_dir))
    return plan


@contextmanager
def _open_archive(source: Path | BinaryIO, limits: ArchiveLimits) -> Iterator[zipfile.ZipFile]:
    try:
        with ExitStack() as stack:
            stream = (
                stack.enter_context(Path(source).open("rb"))
                if isinstance(source, (str, os.PathLike)) else source
            )
            _check_container(stream, limits)
            archive = stack.enter_context(zipfile.ZipFile(stream))
            # Outer icons containers need a larger allowance than a single PNG.
            _guard_members(archive.infolist(), limits=limits, member_limit=limits.max_archive_bytes)
            yield archive
    except _ARCHIVE_ERRORS as exc:
        raise _format_error(f"ZIP 读取失败：{exc}") from exc


def _entry(archive: zipfile.ZipFile, entry: str) -> zipfile.ZipInfo:
    try:
        return archive.getinfo(entry)
    except KeyError as exc:
        raise _format_error(f"缺少条目 {entry!r}，它可能不是受支持的主题包格式") from exc


def _copy_member(
    archive: zipfile.ZipFile,
    member: zipfile.ZipInfo,
    output: BinaryIO,
    maximum: int,
    limits: ArchiveLimits,
) -> int:
    _check_member_size(member, maximum, limits)
    copied = 0
    with archive.open(member) as source:
        while True:
            # The +1 detects a lying header/reader without unbounded read().
            block = source.read(min(_COPY_CHUNK, maximum - copied + 1))
            if not block:
                break
            copied += len(block)
            if copied > maximum or copied > member.file_size:
                raise _format_error(f"条目实际解压大小超过限制 {member.filename!r}")
            output.write(block)
    if copied != member.file_size:
        raise _format_error(f"条目解压长度不一致 {member.filename!r}")
    return copied


def read_entry_bytes(
    archive_path: Path,
    entry: str,
    *,
    limits: ArchiveLimits = DEFAULT_ARCHIVE_LIMITS,
    max_bytes: int | None = None,
) -> bytes:
    """Return a bounded, CRC-checked entry; reject ambiguous archive names."""

    maximum = limits.max_member_bytes if max_bytes is None else min(max_bytes, limits.max_member_bytes)
    with _open_archive(archive_path, limits) as archive:
        output = io.BytesIO()
        _copy_member(archive, _entry(archive, entry), output, maximum, limits)
        return output.getvalue()


def read_entry_text(
    archive_path: Path, entry: str, *, limits: ArchiveLimits = DEFAULT_ARCHIVE_LIMITS,
) -> str:
    """Read a bounded text entry, tolerating BOMs and legacy encodings."""

    raw = read_entry_bytes(archive_path, entry, limits=limits, max_bytes=limits.max_text_bytes)
    for encoding in TEXT_ENCODINGS:
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def has_entry(
    archive_path: Path, entry: str, *, limits: ArchiveLimits = DEFAULT_ARCHIVE_LIMITS,
) -> bool:
    with _open_archive(archive_path, limits) as archive:
        return entry in archive.namelist()


def extract_nested_zip(
    archive_path: Path,
    entry: str,
    destination: Path,
    *,
    limits: ArchiveLimits = DEFAULT_ARCHIVE_LIMITS,
) -> None:
    """Extract one nested ZIP, with independent container and output budgets."""

    with (
        _open_archive(archive_path, limits) as outer,
        tempfile.SpooledTemporaryFile(max_size=8 * _MIB, mode="w+b") as payload,
    ):
        _copy_member(outer, _entry(outer, entry), payload, limits.max_archive_bytes, limits)
        payload.seek(0)
        with _open_archive(payload, limits) as inner:
            safe_extract(inner, destination, limits=limits)


def extract_prefix(
    archive_path: Path,
    prefix: str,
    destination: Path,
    *,
    limits: ArchiveLimits = DEFAULT_ARCHIVE_LIMITS,
) -> None:
    """Extract a directory prefix, retaining that prefix in the output paths."""

    prefix_parts, _ = _member_path(zipfile.ZipInfo(prefix.replace("\\", "/").rstrip("/") + "/"))
    normalized = "/".join(prefix_parts) + "/"
    with _open_archive(archive_path, limits) as archive:
        members = [
            member for member in archive.infolist()
            if member.filename.replace("\\", "/").startswith(normalized)
        ]
        if not members:
            raise _format_error(f"找不到 {prefix!r} 下的资源")
        _extract_members(archive, members, destination, limits)


def _check_existing(path: Path, *, directory: bool) -> None:
    """Refuse links, junctions and special files, including dangling links."""

    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise _format_error(f"解压目标含符号链接或重解析点 {path.name!r}")
    if directory:
        valid = stat.S_ISDIR(info.st_mode)
    else:
        valid = stat.S_ISREG(info.st_mode) and info.st_nlink == 1
    if not valid:
        raise _format_error(f"解压目标类型冲突或存在硬链接 {path.name!r}")


def _extract_members(
    archive: zipfile.ZipFile,
    members: list[zipfile.ZipInfo],
    destination: Path,
    limits: ArchiveLimits,
) -> None:
    plan = _guard_members(members, limits=limits, member_limit=limits.max_member_bytes)
    destination = Path(destination)
    _check_existing(destination, directory=True)
    root = destination.resolve()
    # Preflight *all* destinations before making directories or opening files.
    for _, parts, is_dir in plan:
        target = root
        for depth, part in enumerate(parts, 1):
            target = target / part
            _check_existing(target, directory=depth < len(parts) or is_dir)
        resolved = target.resolve()
        if resolved != root and root not in resolved.parents:
            raise _format_error("解压目标逃离输出目录")
    root.mkdir(parents=True, exist_ok=True)
    total = 0
    for member, parts, is_dir in plan:
        target = root.joinpath(*parts)
        if is_dir:
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(mode="w+b", prefix=".themeshift-", dir=target.parent, delete=False) as output:
                temporary = Path(output.name)
                total += _copy_member(
                    archive, member, output,
                    min(limits.max_member_bytes, limits.max_total_bytes - total), limits,
                )
            _check_existing(target, directory=False)
            os.replace(temporary, target)
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def safe_extract(
    archive: zipfile.ZipFile,
    destination: Path,
    *,
    limits: ArchiveLimits = DEFAULT_ARCHIVE_LIMITS,
) -> None:
    """Stream a stored/deflated ZIP after portable path and budget preflight."""

    try:
        if archive.fp is None:
            raise _format_error("ZIP 已关闭")
        _check_container(archive.fp, limits)
        _extract_members(archive, archive.infolist(), destination, limits)
    except _ARCHIVE_ERRORS as exc:
        raise _format_error(f"ZIP 解压失败：{exc}") from exc


def _directory_entries(arcname: str) -> list[str]:
    """Return the missing parent folders of ``arcname``, outermost first."""

    parts = arcname.split("/")[:-1]
    return ["/".join(parts[:index]) + "/" for index in range(1, len(parts) + 1)]


def write_zip(
    destination: Path,
    files: list[tuple[str, Path]],
    *,
    deterministic: bool = True,
) -> None:
    """Write ``(arcname, path)`` pairs into a deflated zip atomically.

    Parent folders are written explicitly with the DOS directory attribute and
    before their contents. Several vendor theme parsers read folder entries
    instead of inferring implicit directories from member paths.
    """

    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = destination.with_name(destination.name + ".tmp")
    written: set[str] = set()
    with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for arcname, path in files:
            if not deterministic:
                archive.write(path, arcname)
                continue
            if arcname.endswith("/"):
                info = zipfile.ZipInfo(arcname, date_time=FIXED_TIMESTAMP)
                info.create_system = 0
                info.compress_type = zipfile.ZIP_STORED
                info.external_attr = DOS_DIRECTORY_ATTRIBUTE
                archive.writestr(info, b"")
                written.add(arcname)
                continue
            for directory in _directory_entries(arcname):
                if directory in written:
                    continue
                folder = zipfile.ZipInfo(directory, date_time=FIXED_TIMESTAMP)
                folder.create_system = 0
                folder.compress_type = zipfile.ZIP_STORED
                folder.external_attr = DOS_DIRECTORY_ATTRIBUTE
                archive.writestr(folder, b"")
                written.add(directory)
            info = zipfile.ZipInfo(arcname, date_time=FIXED_TIMESTAMP)
            info.create_system = 0
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = DOS_ARCHIVE_ATTRIBUTE
            # Stream instead of read_bytes(): large nested archives no longer
            # have to be fully resident just to be copied into the outer ZIP.
            with Path(path).open("rb") as source, archive.open(info, "w") as target:
                shutil.copyfileobj(source, target, length=1024 * 1024)
    os.replace(tmp_path, destination)


def write_bytes_atomic(destination: Path, payload: bytes) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = destination.with_name(destination.name + ".tmp")
    tmp_path.write_bytes(payload)
    os.replace(tmp_path, destination)


def write_text_lf(destination: Path, text: str) -> bytes:
    """Write UTF-8 without a BOM and with Unix line endings only."""

    payload = text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
    write_bytes_atomic(destination, payload)
    return payload
