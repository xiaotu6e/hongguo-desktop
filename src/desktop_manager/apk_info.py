from __future__ import annotations

import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .config import HONGGUO, PACKAGE_RE


@dataclass(frozen=True)
class ApkInfo:
    path: Path
    package: str
    name: str
    version: str
    version_code: int


def inspect_apk(filename: str | Path) -> ApkInfo:
    path = Path(filename).expanduser().resolve()
    if not path.is_file() or path.suffix.lower() != ".apk":
        raise ValueError("请拖入 .apk 安装包；文件夹、XAPK 和 APKs 暂不支持。")
    if path.stat().st_size > 2 * 1024**3:
        raise ValueError("安装包超过 2 GB，暂不支持。")
    try:
        with zipfile.ZipFile(path) as archive:
            entry = archive.getinfo("AndroidManifest.xml")
            if entry.file_size > 4 * 1024**2:
                raise ValueError("安装包清单异常，无法安装。")
            raw = archive.read(entry)
        if raw.lstrip().startswith(b"<"):
            manifest = ET.fromstring(raw)
        else:
            from apkutils import APK

            apk = APK.from_file(str(path))
            try:
                manifest = ET.fromstring(apk.get_manifest())
            finally:
                apk.close()
        package = manifest.get("package", "")
        if not PACKAGE_RE.fullmatch(package):
            raise ValueError("安装包缺少有效的应用包名。")
        ns = "{http://schemas.android.com/apk/res/android}"
        application = manifest.find("application")
        label = application.get(ns + "label", "") if application is not None else ""
        name = "红果短剧" if package == HONGGUO else label if label and not label.startswith("@") else package
        return ApkInfo(path, package, name, manifest.get(ns + "versionName", "未知版本"), int(manifest.get(ns + "versionCode", "0")))
    except (zipfile.BadZipFile, KeyError, ET.ParseError) as error:
        raise ValueError("这不是完整的 APK 安装包，或文件已经损坏。") from error
