#!/usr/bin/env python3
import argparse
import json
import os
import shutil
import stat
import subprocess
import sys


PACKAGE_MANAGERS = {
    "apt": {
        "executable": "apt-get",
        "packages": [
            "python3-venv",
            "python3-wxgtk4.0",
            "python3-termcolor",
            "p7zip-full",
            "grub2-common",
            "grub-pc-bin",
            "parted",
            "dosfstools",
            "ntfs-3g",
        ],
        "commands": [(["update"], False), (["install", "-y", "--"], True)],
    },
    "dnf": {
        "executable": "dnf",
        "packages": [
            "python3",
            "python3-pip",
            "python3-wxpython4",
            "python3-termcolor",
            "p7zip",
            "p7zip-plugins",
            "grub2-tools",
            "grub2-tools-extra",
            "parted",
            "dosfstools",
            "ntfs-3g",
        ],
        "commands": [(["install", "-y", "--"], True)],
    },
    "yum": {
        "executable": "yum",
        "packages": [
            "python3",
            "python3-pip",
            "python3-wxpython4",
            "python3-termcolor",
            "p7zip",
            "p7zip-plugins",
            "grub2-tools",
            "grub2-tools-extra",
            "parted",
            "dosfstools",
            "ntfs-3g",
        ],
        "commands": [(["install", "-y", "--"], True)],
    },
    "pacman": {
        "executable": "pacman",
        "packages": [
            "python",
            "python-pip",
            "python-wxpython",
            "python-termcolor",
            "7zip",
            "grub",
            "parted",
            "dosfstools",
            "ntfs-3g",
        ],
        "commands": [(["-S", "--needed", "--noconfirm", "--"], True)],
    },
    "zypper": {
        "executable": "zypper",
        "packages": [
            "python3",
            "python3-pip",
            "python3-wxPython",
            "python3-termcolor",
            "p7zip",
            "grub2",
            "parted",
            "dosfstools",
            "ntfs-3g",
        ],
        "commands": [
            (["--non-interactive", "refresh"], False),
            (["install", "--non-interactive", "--"], True),
        ],
    },
    "apk": {
        "executable": "apk",
        "packages": [
            "python3",
            "py3-pip",
            "py3-wxpython",
            "py3-termcolor",
            "p7zip",
            "grub",
            "parted",
            "dosfstools",
            "ntfs-3g",
        ],
        "commands": [(["add", "--no-cache", "--"], True)],
    },
    "xbps-install": {
        "executable": "xbps-install",
        "packages": [
            "python3",
            "python3-pip",
            "python3-wxPython",
            "python3-termcolor",
            "p7zip",
            "grub",
            "parted",
            "dosfstools",
            "ntfs-3g",
        ],
        "commands": [(["-S", "-y", "--"], True)],
    },
}


def fail(message):
    print(message, file=sys.stderr, flush=True)
    raise SystemExit(1)


def load_block_devices():
    result = subprocess.run(
        [
            "lsblk",
            "--json",
            "--tree",
            "--bytes",
            "--output",
            "PATH,TYPE,SIZE,TRAN,RM,RO,MAJ:MIN,MOUNTPOINTS",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout).get("blockdevices", [])


def descendants(device):
    yield device
    for child in device.get("children", []):
        yield from descendants(child)


def get_usb_disk(target_path):
    if os.geteuid() != 0:
        fail("Помощник записи должен быть запущен с правами администратора.")

    target_path = os.path.realpath(target_path)

    try:
        target_stat = os.stat(target_path)
    except OSError as error:
        fail(f"Не удалось открыть накопитель: {error}")

    if not stat.S_ISBLK(target_stat.st_mode):
        fail("Цель записи не является блочным устройством.")

    try:
        devices = load_block_devices()
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as error:
        fail(f"Не удалось проверить накопитель через lsblk: {error}")

    disk = next(
        (
            device
            for device in devices
            if device.get("type") == "disk"
            and device.get("path")
            and os.path.realpath(device["path"]) == target_path
        ),
        None,
    )
    if disk is None:
        fail("Выбранное устройство не является целым диском.")
    if not (disk.get("rm") or (disk.get("tran") or "").lower() == "usb"):
        fail("Для защиты разрешена запись только на USB или съёмный диск.")
    return disk, target_path


def validate(image_path, target_path):
    image_path = os.path.realpath(image_path)
    try:
        image_stat = os.stat(image_path)
    except OSError as error:
        fail(f"Не удалось открыть файл образа: {error}")
    if not stat.S_ISREG(image_stat.st_mode):
        fail("Образ должен быть обычным файлом.")
    if image_stat.st_size <= 0:
        fail("Файл образа пуст.")

    disk, target_path = get_usb_disk(target_path)
    if disk.get("ro"):
        fail("Выбранный накопитель доступен только для чтения.")

    device_tree = list(descendants(disk))
    mountpoints = [
        point
        for device in device_tree
        for point in (device.get("mountpoints") or [])
        if point
    ]
    if mountpoints:
        fail(
            "Разделы накопителя смонтированы. Сначала извлеките/размонтируйте "
            "их в файловом менеджере и повторите попытку."
        )

    try:
        source_result = subprocess.run(
            ["findmnt", "--json", "--target", image_path, "--output", "MAJ:MIN"],
            check=True,
            capture_output=True,
            text=True,
        )
        source_data = json.loads(source_result.stdout)
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as error:
        fail(f"Не удалось проверить, где расположен файл образа: {error}")
    source_filesystems = source_data.get("filesystems", [])
    source_device = source_filesystems[0].get("maj:min") if source_filesystems else None
    if not source_device:
        fail("Не удалось определить устройство, на котором находится файл образа.")
    target_devices = {device.get("maj:min") for device in device_tree}
    if source_device in target_devices:
        fail("Файл образа находится на том же накопителе, который выбран для записи.")

    size = int(disk.get("size") or 0)
    if image_stat.st_size > size:
        fail("Файл образа больше выбранного накопителя.")

    return image_path, target_path


def unmount_disk(target_path):
    disk, _ = get_usb_disk(target_path)
    mountpoints = sorted(
        {
            point
            for device in descendants(disk)
            for point in (device.get("mountpoints") or [])
            if point
        },
        key=lambda point: (point.count("/"), len(point)),
        reverse=True,
    )
    if not mountpoints:
        print("На выбранном диске нет смонтированных разделов.", flush=True)
        return

    umount = shutil.which("umount")
    if not umount:
        fail("Не найдена команда umount (установите util-linux).")

    for mountpoint in mountpoints:
        print(f"Размонтирую {mountpoint}…", flush=True)
        result = subprocess.run(
            [umount, "--", mountpoint],
            capture_output=True,
            text=True,
        )
        if result.returncode:
            details = (result.stderr or result.stdout).strip()
            fail(
                f"Не удалось размонтировать {mountpoint}: "
                f"{details or f'код завершения {result.returncode}'}"
            )
    print("Все разделы USB-диска размонтированы.", flush=True)


def write_windows_image(image_path, target_path, woeusb):
    image_path = os.path.realpath(image_path)
    if os.path.splitext(image_path)[1].lower() != ".iso":
        fail("Для создания Windows USB требуется файл ISO.")
    try:
        image_stat = os.stat(image_path)
    except OSError as error:
        fail(f"Не удалось открыть Windows ISO: {error}")
    if not stat.S_ISREG(image_stat.st_mode) or image_stat.st_size <= 0:
        fail("Windows ISO должен быть непустым обычным файлом.")

    disk, target_path = get_usb_disk(target_path)
    if disk.get("ro"):
        fail("Выбранный накопитель доступен только для чтения.")

    device_tree = list(descendants(disk))
    mountpoints = [
        point
        for device in device_tree
        for point in (device.get("mountpoints") or [])
        if point
    ]
    if mountpoints:
        fail(
            "Разделы накопителя смонтированы. Нажмите «Размонтировать» "
            "и повторите попытку."
        )

    try:
        source_result = subprocess.run(
            ["findmnt", "--json", "--target", image_path, "--output", "MAJ:MIN"],
            check=True,
            capture_output=True,
            text=True,
        )
        source_data = json.loads(source_result.stdout)
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as error:
        fail(f"Не удалось проверить, где находится файл образа: {error}")
    source_filesystems = source_data.get("filesystems", [])
    source_device = source_filesystems[0].get("maj:min") if source_filesystems else None
    if not source_device:
        fail("Не удалось определить устройство, на котором находится файл образа.")
    target_devices = {device.get("maj:min") for device in device_tree}
    if source_device in target_devices:
        fail("Файл образа находится на том же накопителе, который выбран для записи.")

    woeusb = os.path.realpath(woeusb)
    if not os.path.isfile(woeusb) or not os.access(woeusb, os.X_OK):
        fail("Исполняемый файл WoeUSB-ng не найден или недоступен.")

    print("Создаю установочную флешку Windows через WoeUSB-ng.", flush=True)
    try:
        process = subprocess.Popen(
            [woeusb, "--device", image_path, target_path],
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
        fail(f"Не удалось запустить WoeUSB-ng: {error}")

    if return_code:
        fail(
            f"WoeUSB-ng завершился с кодом {return_code}. "
            "Флешка могла быть изменена не полностью."
        )
    print("\nУстановочная флешка Windows готова.", flush=True)


def install_woeusb_dependencies():
    if os.geteuid() != 0:
        fail("Установка системных зависимостей требует прав администратора.")

    manager = None
    for name, config in PACKAGE_MANAGERS.items():
        executable = shutil.which(config["executable"])
        if executable:
            manager = name, executable, config
            break
    if not manager:
        supported = ", ".join(PACKAGE_MANAGERS)
        fail(
            "Не найден поддерживаемый пакетный менеджер. "
            f"Поддерживаются: {supported}."
        )

    manager_name, executable, config = manager
    print(f"Пакетный менеджер: {manager_name}", flush=True)
    for step, include_packages in config["commands"]:
        command = [executable, *step]
        if include_packages:
            command.extend(config["packages"])
        print(f"Запускаю: {' '.join(command)}", flush=True)
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
            fail(f"Не удалось запустить {manager_name}: {error}")
        if return_code:
            fail(f"{manager_name} завершился с кодом {return_code}.")

    print("Системные зависимости установлены.", flush=True)


def write_image(image_path, target_path):
    image_path, target_path = validate(image_path, target_path)
    print("Начинаю запись. Не отключайте накопитель.", flush=True)
    try:
        process = subprocess.Popen(
            [
                "dd",
                f"if={image_path}",
                f"of={target_path}",
                "bs=4M",
                "status=progress",
                "conv=fsync",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        while True:
            chunk = os.read(process.stderr.fileno(), 4096)
            if not chunk:
                break
            sys.stdout.buffer.write(chunk)
            sys.stdout.buffer.flush()
        return_code = process.wait()
    except OSError as error:
        fail(f"Не удалось запустить dd: {error}")

    if return_code:
        fail(f"dd завершился с кодом {return_code}. Образ мог записаться не полностью.")
    print("\nЗапись завершена.", flush=True)


def main():
    parser = argparse.ArgumentParser(description="Запись образа на USB-накопитель")
    operation = parser.add_mutually_exclusive_group(required=True)
    operation.add_argument(
        "--write", nargs=2, metavar=("IMAGE", "DEVICE")
    )
    operation.add_argument("--unmount", metavar="DEVICE")
    operation.add_argument(
        "--windows-image", nargs=3, metavar=("ISO", "DEVICE", "WOEUSB")
    )
    operation.add_argument(
        "--install-woeusb-deps",
        action="store_true",
        help="Install WoeUSB-ng system dependencies on Debian-based systems",
    )
    args = parser.parse_args()
    if args.write:
        write_image(*args.write)
    elif args.windows_image:
        write_windows_image(*args.windows_image)
    elif args.install_woeusb_deps:
        install_woeusb_dependencies()
    else:
        unmount_disk(args.unmount)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
