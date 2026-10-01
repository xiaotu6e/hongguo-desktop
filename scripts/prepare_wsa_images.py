"""Prepare WSA compatibility images under WSL; never deploy them here.

Port of the locally verified NDK and video repairs. Only the recorded original
images are accepted. Windows deployment and restoration are in repair-wsa.ps1.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import xml.etree.ElementTree as ET


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(4 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def run(*args: str, allowed: tuple[int, ...] = (0,)) -> str:
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode not in allowed:
        raise RuntimeError(f"{args[0]} failed ({result.returncode}): {result.stdout}\n{result.stderr}")
    return result.stdout


def validate_originals(installation: Path, manifest: dict) -> None:
    for name in ("system", "vendor"):
        path = installation / f"{name}.vhdx"
        if sha256(path) != manifest["images"][name]["original_sha256"]:
            raise RuntimeError(f"Unsupported or modified {name}.vhdx; no files were changed")


def remove_hevc_declaration(config: bytes) -> bytes:
    tree = ET.fromstring(config)
    decoders = tree.find("Decoders")
    if decoders is None:
        raise RuntimeError("Unsupported video codec XML")
    matching = [node for node in decoders if node.get("name") == "OMX.android.latte.hevc.decoder"]
    if len(matching) != 1:
        raise RuntimeError("Expected exactly one WSA HEVC decoder declaration")
    decoders.remove(matching[0])
    return ET.tostring(tree, encoding="utf-8", xml_declaration=True)


def copy_translation(archive_path: Path, system: Path) -> None:
    library_context = os.getxattr(system / "lib64/libc++.so", "security.selinux")
    directory_context = os.getxattr(system / "lib64", "security.selinux")
    binary_context = b"u:object_r:system_file:s0\x00"
    etc_context = os.getxattr(system / "etc/hosts", "security.selinux")
    arm = system / "lib64/arm64"
    if not arm.is_symlink():
        raise RuntimeError("Unexpected ARM64 directory layout")
    arm.unlink()
    arm.mkdir()
    os.chmod(arm, 0o755)
    os.setxattr(arm, "security.selinux", directory_context)
    with tarfile.open(archive_path) as archive:
        for member in archive:
            if not member.isfile() or "/template/system/" not in member.name:
                continue
            relative = member.name.split("/template/system/", 1)[1]
            if Path(relative).is_absolute() or ".." in Path(relative).parts:
                raise RuntimeError("Archive path escapes the candidate image")
            if relative == "lib/libautobridge.so":
                relative = "lib/libndk_translation.so"
            elif "libautobridge" in relative or relative.startswith(("etc/init/", "etc/binfmt_misc/")):
                continue
            relative = relative.replace("lib64/arm64_ndk/", "lib64/arm64/")
            target = system / relative
            if target.is_symlink() or not target.resolve().is_relative_to(system.resolve()):
                raise RuntimeError("Refusing write through an image symlink")
            target.parent.mkdir(parents=True, exist_ok=True)
            stream = archive.extractfile(member)
            if stream is None:
                raise RuntimeError("Archive member is unreadable")
            with stream:
                target.write_bytes(stream.read())
            executable = relative.startswith("bin/")
            os.chmod(target, 0o755 if executable else 0o644)
            context = binary_context if executable else library_context if relative.startswith("lib") else etc_context
            os.setxattr(target, "security.selinux", context)
            if relative.startswith("bin/arm64/"):
                os.chmod(target.parent, 0o755)
                os.setxattr(target.parent, "security.selinux", binary_context)


def prepare(installation: Path, backup: Path, work: Path, archive: Path, manifest: dict) -> dict:
    validate_originals(installation, manifest)
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        raise RuntimeError("Run this preparation script as root inside WSL")
    if backup.resolve() == installation.resolve() or work.resolve() == installation.resolve():
        raise RuntimeError("Backup/work directory must differ from the WSA installation")
    for tool in ("qemu-img", "e2fsck", "resize2fs", "mount", "umount"):
        if not shutil.which(tool):
            raise RuntimeError(f"Missing Linux tool: {tool}. See docs/compatibility.md")
    backup.mkdir(parents=True, exist_ok=True)
    for name in ("system", "vendor"):
        original = backup / f"{name}.vhdx"
        if not original.exists():
            shutil.copy2(installation / original.name, original)
        if sha256(original) != manifest["images"][name]["original_sha256"]:
            raise RuntimeError("Backup checksum mismatch; originals remain untouched")
    work.mkdir(parents=True, exist_ok=False)
    mounted: list[Path] = []
    try:
        for name, extra_mb in (("system", 128), ("vendor", 16)):
            raw = work / f"{name}.img"
            mount = work / name
            mount.mkdir()
            run("qemu-img", "convert", "-f", "vhdx", "-O", "raw", str(backup / f"{name}.vhdx"), str(raw))
            with raw.open("r+b") as stream:
                stream.truncate(raw.stat().st_size + extra_mb * 1024**2)
            run("e2fsck", "-f", "-y", str(raw), allowed=(0, 1))
            run("resize2fs", str(raw))
            run("mount", "-o", "loop", str(raw), str(mount))
            mounted.append(mount)
        system, vendor = work / "system/system", work / "vendor"
        copy_translation(archive, system)
        prop = vendor / "build.prop"
        text = prop.read_text()
        if text.count("ro.dalvik.vm.native.bridge=libhoudini.so") != 1:
            raise RuntimeError("Unexpected native bridge property")
        prop.write_text(text.replace("ro.dalvik.vm.native.bridge=libhoudini.so", "ro.dalvik.vm.native.bridge=libndk_translation.so"))
        codecs = vendor / "etc/media_codecs_google_video.xml"
        (backup / "media_codecs_google_video.xml").write_bytes(codecs.read_bytes())
        codecs.write_bytes(remove_hevc_declaration(codecs.read_bytes()))
    finally:
        unmount_errors = []
        for mount in reversed(mounted):
            try:
                run("umount", str(mount))
            except RuntimeError as error:
                unmount_errors.append(str(error))
        if unmount_errors:
            raise RuntimeError("Unmount failed; do not deploy candidates: " + "; ".join(unmount_errors))
    result = {"images": {}}
    for name in ("system", "vendor"):
        raw, candidate = work / f"{name}.img", work / f"{name}.vhdx"
        run("e2fsck", "-f", "-n", str(raw))
        run("qemu-img", "convert", "-f", "raw", "-O", "vhdx", str(raw), str(candidate))
        result["images"][name] = {"filename": candidate.name, "sha256": sha256(candidate)}
    (work / "candidate-manifest.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("installation", "backup", "work", "source-archive", "manifest", "dependencies"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8-sig"))
    dependencies = json.loads(args.dependencies.read_text(encoding="utf-8-sig"))
    if sha256(args.source_archive) != dependencies["autobridge"]["sha256"]:
        raise RuntimeError("AutoBridge source checksum mismatch")
    result = prepare(args.installation, args.backup, args.work, args.source_archive, manifest)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
