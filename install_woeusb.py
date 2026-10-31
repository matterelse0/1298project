#!/usr/bin/env python3
import argparse
import hashlib
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
from pathlib import Path, PurePosixPath


VERSION = "0.2.12"
PYPI_URL = f"https://pypi.org/pypi/WoeUSB-ng/{VERSION}/json"


def fail(message):
    print(message, file=sys.stderr, flush=True)
    raise SystemExit(1)


def download_source(destination):
    print(f"Получаю метаданные WoeUSB-ng {VERSION} с PyPI…", flush=True)
    try:
        with urllib.request.urlopen(PYPI_URL, timeout=30) as response:
            release = json.load(response)
        source = next(
            item for item in release["urls"] if item["packagetype"] == "sdist"
        )
        with urllib.request.urlopen(source["url"], timeout=60) as response:
            archive = response.read()
    except (OSError, urllib.error.URLError, StopIteration, KeyError, ValueError) as error:
        fail(f"Не удалось скачать исходники WoeUSB-ng: {error}")

    digest = hashlib.sha256(archive).hexdigest()
    if digest != source.get("digests", {}).get("sha256"):
        fail("Контрольная сумма архива WoeUSB-ng не совпала.")
    destination.write_bytes(archive)
    print("Архив скачан и проверен.", flush=True)


def extract_source(archive_path, source_dir, temporary_dir):
    expected_root = f"WoeUSB-ng-{VERSION}"
    try:
        with tarfile.open(archive_path, "r:gz") as archive:
            members = archive.getmembers()
            for member in members:
                member_path = PurePosixPath(member.name)
                if (
                    member_path.is_absolute()
                    or ".." in member_path.parts
                    or not member_path.parts
                    or member_path.parts[0] != expected_root
                    or not (member.isdir() or member.isfile())
                ):
                    fail("Архив WoeUSB-ng содержит недопустимый путь или тип файла.")
            archive.extractall(temporary_dir, filter="data")
    except (OSError, tarfile.TarError) as error:
        fail(f"Не удалось распаковать WoeUSB-ng: {error}")

    extracted = temporary_dir / expected_root
    if not (extracted / "setup.py").is_file() or not (
        extracted / "WoeUSB" / "woeusb"
    ).is_file():
        fail("В архиве WoeUSB-ng отсутствуют необходимые файлы.")
    extracted.rename(source_dir)


def install(source_dir):
    command = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--no-deps",
        "--editable",
        str(source_dir),
    ]
    print(
        "Устанавливаю WoeUSB-ng без системного post-install: "
        f"{' '.join(command)}",
        flush=True,
    )
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,
        )
        while True:
            chunk = os.read(process.stdout.fileno(), 4096)
            if not chunk:
                break
            sys.stdout.buffer.write(chunk)
            sys.stdout.buffer.flush()
        return_code = process.wait()
    except OSError as error:
        fail(f"Не удалось запустить pip для установки WoeUSB-ng: {error}")
    if return_code:
        fail(f"Установка WoeUSB-ng завершилась с кодом {return_code}.")

    executable = Path(sys.prefix) / "bin" / "woeusb"
    if not executable.is_file() or not os.access(executable, os.X_OK):
        fail(f"Установка завершилась, но команда не найдена: {executable}")
    print("WoeUSB-ng установлен в пользовательское окружение.", flush=True)


def main():
    parser = argparse.ArgumentParser(
        description="Безопасная установка WoeUSB-ng в активное Python-окружение."
    )
    parser.add_argument("--source-root", required=True, type=Path)
    args = parser.parse_args()

    source_dir = args.source_root / f"WoeUSB-ng-{VERSION}"
    if source_dir.exists():
        if not (source_dir / "setup.py").is_file() or not (
            source_dir / "WoeUSB" / "woeusb"
        ).is_file():
            fail(
                f"Каталог исходников существует, но повреждён: {source_dir}. "
                "Уберите этот каталог и повторите установку."
            )
    else:
        try:
            args.source_root.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(
                prefix=".woeusb-download-", dir=args.source_root
            ) as temporary:
                temporary_dir = Path(temporary)
                archive_path = temporary_dir / "woeusb-ng.tar.gz"
                extracted_root = temporary_dir / "extracted"
                extracted_root.mkdir()
                download_source(archive_path)
                extract_source(archive_path, source_dir, extracted_root)
        except OSError as error:
            fail(f"Не удалось подготовить каталог WoeUSB-ng: {error}")

    install(source_dir)


if __name__ == "__main__":
    main()
