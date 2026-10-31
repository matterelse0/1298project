#!/usr/bin/env python3
import hmac
import json
import os
import secrets
import shutil
import stat
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT / "web"
MAX_UPLOAD_SIZE = 512 * 1024**3
ALLOWED_IMAGE_EXTENSIONS = {".img", ".iso", ".bin", ".raw", ".dsk"}


def format_size(size):
    value = float(size)
    for unit in ("Б", "КиБ", "МиБ", "ГиБ", "ТиБ"):
        if value < 1024 or unit == "ТиБ":
            return f"{value:.1f} {unit}"
        value /= 1024


def get_usb_disks():
    result = subprocess.run(
        [
            "lsblk",
            "--json",
            "--tree",
            "--bytes",
            "--output",
            "PATH,TYPE,SIZE,MODEL,TRAN,RM,RO,MOUNTPOINTS",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    data = json.loads(result.stdout)
    return [
        device
        for device in data.get("blockdevices", [])
        if device.get("type") == "disk"
        and (device.get("rm") or (device.get("tran") or "").lower() == "usb")
    ]


def has_mounted_partitions(device):
    if any(device.get("mountpoints") or []):
        return True
    return any(has_mounted_partitions(child) for child in device.get("children", []))


def find_woeusb():
    executable = shutil.which("woeusb")
    if executable:
        return executable
    user_install = Path.home() / ".local" / "bin" / "woeusb"
    if user_install.is_file() and os.access(user_install, os.X_OK):
        return str(user_install)
    managed_install = (
        Path.home()
        / ".local"
        / "share"
        / "usb-image-writer"
        / "woeusb"
        / "bin"
        / "woeusb"
    )
    if managed_install.is_file() and os.access(managed_install, os.X_OK):
        return str(managed_install)
    return None


class ImageWriterEngine:
    def __init__(self):
        self.lock = threading.RLock()
        self.busy = False
        self.status = "Готово к работе"
        self.operation = None
        self.result = None
        self.logs = deque(maxlen=3000)
        self.log_sequence = 0
        self.image = None
        self.woeusb_check = None
        self.image_dir = Path.home() / ".cache" / "usb-image-writer" / "images"
        self.venv_dir = (
            Path.home() / ".local" / "share" / "usb-image-writer" / "woeusb"
        )
        self.cache_dir = Path.home() / ".cache" / "usb-image-writer" / "pip"

    def log(self, message):
        with self.lock:
            for line in str(message).splitlines() or [""]:
                self.log_sequence += 1
                self.logs.append(
                    {
                        "id": self.log_sequence,
                        "time": time.strftime("%H:%M:%S"),
                        "text": line,
                    }
                )

    def set_status(self, status):
        with self.lock:
            self.status = status

    def get_state(self, after=0):
        try:
            disks = get_usb_disks()
            disk_error = None
        except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as error:
            disks = []
            disk_error = str(error)

        with self.lock:
            image = dict(self.image) if self.image else None
            state = {
                "busy": self.busy,
                "status": self.status,
                "operation": self.operation,
                "result": self.result,
                "image": image,
                "disks": [
                    {
                        "path": disk.get("path"),
                        "model": (disk.get("model") or "USB-диск").strip(),
                        "size": int(disk.get("size") or 0),
                        "size_label": format_size(int(disk.get("size") or 0)),
                        "read_only": bool(disk.get("ro")),
                        "mounted": has_mounted_partitions(disk),
                    }
                    for disk in disks
                ],
                "disk_error": disk_error,
                "woeusb": {
                    "path": find_woeusb(),
                    "check": self.woeusb_check,
                },
                "logs": [entry for entry in self.logs if entry["id"] > after],
                "last_log_id": self.log_sequence,
            }
        return state

    def set_image_path(self, path):
        if not isinstance(path, str) or not path.strip():
            raise ValueError("Укажи полный путь к образу.")
        with self.lock:
            if self.busy:
                raise RuntimeError("Нельзя менять образ во время операции.")
        image_path = Path(path).expanduser().resolve(strict=True)
        image_stat = image_path.stat()
        if not stat.S_ISREG(image_stat.st_mode):
            raise ValueError("Выбранный путь не является обычным файлом.")
        if image_stat.st_size <= 0:
            raise ValueError("Файл образа пуст.")
        if image_path.suffix.lower() not in ALLOWED_IMAGE_EXTENSIONS:
            raise ValueError("Поддерживаются образы .img, .iso, .bin, .raw и .dsk.")
        with self.lock:
            self.image = {
                "path": str(image_path),
                "name": image_path.name,
                "size": image_stat.st_size,
                "size_label": format_size(image_stat.st_size),
                "uploaded": False,
            }
        self.log(f"Выбран образ: {image_path.name} ({format_size(image_stat.st_size)})")

    def store_upload(self, filename, content_length, source):
        with self.lock:
            if self.busy:
                raise RuntimeError("Нельзя менять образ во время операции.")
        if content_length <= 0:
            raise ValueError("Загружаемый файл пуст.")
        if content_length > MAX_UPLOAD_SIZE:
            raise ValueError("Размер файла превышает ограничение 512 ГиБ.")
        safe_name = Path(filename).name
        if not safe_name or safe_name in (".", ".."):
            raise ValueError("Не удалось определить имя образа.")
        if Path(safe_name).suffix.lower() not in ALLOWED_IMAGE_EXTENSIONS:
            raise ValueError("Поддерживаются образы .img, .iso, .bin, .raw и .dsk.")

        self.image_dir.mkdir(parents=True, exist_ok=True)
        destination = self.image_dir / f"{secrets.token_hex(8)}-{safe_name}"
        temporary = destination.with_suffix(destination.suffix + ".part")
        try:
            remaining = content_length
            with temporary.open("xb") as target:
                while remaining:
                    chunk = source.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise ValueError("Передача файла неожиданно оборвалась.")
                    target.write(chunk)
                    remaining -= len(chunk)
            temporary.replace(destination)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

        with self.lock:
            self.image = {
                "path": str(destination),
                "name": safe_name,
                "size": content_length,
                "size_label": format_size(content_length),
                "uploaded": True,
            }
        self.log(f"Образ загружен: {safe_name} ({format_size(content_length)})")
        return dict(self.image)

    def start(self, operation, payload):
        if not isinstance(operation, str):
            raise ValueError("Неизвестная операция.")
        with self.lock:
            if self.busy:
                raise RuntimeError("Дождитесь завершения текущей операции.")
            if operation not in {"write", "unmount", "install", "check"}:
                raise ValueError("Неизвестная операция.")
            if operation in {"write", "unmount", "install"}:
                password = payload.get("password")
                if not isinstance(password, str) or not password:
                    raise ValueError("Введите пароль sudo.")
            else:
                password = None
            if operation in {"write", "unmount", "install"} and payload.get(
                "confirmed"
            ) is not True:
                raise ValueError("Необходимо подтвердить действие.")

            self.busy = True
            self.operation = operation
            self.result = None
            self.status = {
                "write": "Подготовка записи",
                "unmount": "Подготовка размонтирования",
                "install": "Подготовка установки",
                "check": "Проверка WoeUSB-ng",
            }[operation]
            self.logs.clear()
            payload = dict(payload)
            payload.pop("password", None)
            self.log(f"Запущено: {self.status}")

        threading.Thread(
            target=self._run_operation,
            args=(operation, dict(payload), password),
            daemon=True,
        ).start()

    def _run_operation(self, operation, payload, password):
        success = False
        try:
            if operation == "write":
                self._write(payload, password)
            elif operation == "unmount":
                self._run_privileged(
                    ["--unmount", self._validate_disk(payload.get("device"))],
                    password,
                    "Размонтирование разделов",
                )
            elif operation == "install":
                self._install(password)
            elif operation == "check":
                self._check_woeusb()
            success = True
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
            self.log(f"ОШИБКА: {error}")
            self.set_status("Операция завершилась с ошибкой")
        finally:
            with self.lock:
                self.busy = False
                self.result = "success" if success else "error"
                if success:
                    self.status = {
                        "write": "Запись завершена",
                        "unmount": "Разделы размонтированы",
                        "install": "WoeUSB-ng установлен",
                        "check": (
                            "WoeUSB-ng работает"
                            if self.woeusb_check and self.woeusb_check["ok"]
                            else "WoeUSB-ng не найден или не работает"
                        ),
                    }[operation]
                self.log(f"Операция завершена: {'успешно' if success else 'ошибка'}")

    def _validate_disk(self, device_path):
        if not isinstance(device_path, str) or not device_path:
            raise ValueError("Выберите USB-накопитель.")
        disks = get_usb_disks()
        disk = next((item for item in disks if item.get("path") == device_path), None)
        if disk is None:
            raise ValueError("Выбранный диск не найден среди USB-накопителей.")
        return device_path

    def _write(self, payload, password):
        with self.lock:
            image = dict(self.image) if self.image else None
        if not image:
            raise ValueError("Сначала выберите образ.")
        image_path = image["path"]
        disk_path = self._validate_disk(payload.get("device"))
        mode = payload.get("mode")
        if not isinstance(mode, str) or mode not in {"raw", "windows"}:
            raise ValueError("Выберите способ записи.")
        if mode == "windows":
            if Path(image_path).suffix.lower() != ".iso":
                raise ValueError("Для режима Windows требуется файл ISO.")
            woeusb = find_woeusb()
            if not woeusb:
                raise ValueError("WoeUSB-ng не найден. Установите поддержку Windows.")
            arguments = ["--windows-image", image_path, disk_path, woeusb]
        else:
            arguments = ["--write", image_path, disk_path]
        label = "Запись Windows-флешки" if mode == "windows" else "Запись образа"
        self._run_privileged(arguments, password, label)

    def _run_privileged(self, arguments, password, label):
        helper = ROOT / "write_image.py"
        if not helper.is_file():
            raise RuntimeError(f"Файл-помощник не найден: {helper}")
        sudo = shutil.which("sudo")
        if not sudo:
            raise RuntimeError("Команда sudo не найдена.")
        command = [
            sudo,
            "-S",
            "-k",
            "-p",
            "",
            sys.executable,
            str(helper),
            *arguments,
        ]
        self._run_command(command, label, password=password)

    def _install(self, password):
        for directory in (self.venv_dir.parent, self.cache_dir):
            directory.mkdir(parents=True, exist_ok=True)
            if not os.access(directory, os.W_OK | os.X_OK):
                raise PermissionError(f"Нет прав на запись в {directory}")

        self._run_privileged(
            ["--install-woeusb-deps"],
            password,
            "Установка системных зависимостей",
        )
        self._run_command(
            [
                sys.executable,
                "-m",
                "venv",
                "--system-site-packages",
                str(self.venv_dir),
            ],
            "Создание отдельного Python-окружения",
        )
        environment = os.environ.copy()
        environment["PIP_CACHE_DIR"] = str(self.cache_dir)
        environment["TMPDIR"] = str(self.cache_dir)
        self._run_command(
            [
                str(self.venv_dir / "bin" / "python"),
                str(ROOT / "install_woeusb.py"),
                "--source-root",
                str(self.venv_dir.parent / "source"),
            ],
            "Скачивание и установка WoeUSB-ng",
            environment=environment,
        )

    def _check_woeusb(self):
        self.woeusb_check = None
        executable = find_woeusb()
        if not executable:
            self.woeusb_check = {"ok": False, "path": None}
            raise RuntimeError(
                "WoeUSB-ng не найден. Нажмите «Установить поддержку Windows»."
            )
        self._run_command([executable, "--help"], "Проверка команды WoeUSB-ng", timeout=20)

    def _run_command(self, command, label, password=None, environment=None, timeout=None):
        self.set_status(label)
        self.log(f"$ {' '.join(command)}")
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE if password is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=0,
                env=environment,
            )
        except OSError as error:
            raise RuntimeError(f"Не удалось запустить команду: {error}") from error

        if password is not None:
            try:
                process.stdin.write((password + "\n").encode())
                process.stdin.flush()
            except BrokenPipeError:
                pass
            finally:
                process.stdin.close()

        output = bytearray()
        started = time.monotonic()
        while True:
            chunk = os.read(process.stdout.fileno(), 4096)
            if not chunk:
                break
            decoded = chunk.decode("utf-8", errors="replace")
            output.extend(chunk)
            if len(output) > 16000:
                del output[:-12000]
            self.log(decoded)
            if timeout and time.monotonic() - started > timeout:
                process.terminate()
                process.wait(timeout=3)
                raise RuntimeError(f"{label}: превышено время ожидания.")
        return_code = process.wait()
        if return_code:
            details = output.decode("utf-8", errors="replace").strip()
            raise RuntimeError(
                f"{label} завершилась с кодом {return_code}."
                + (f"\n{details[-5000:]}" if details else "")
            )
        if command[-1:] == ["--help"]:
            help_text = output.decode("utf-8", errors="replace")
            if "--device" not in help_text:
                raise RuntimeError(
                    "В справке WoeUSB-ng не найдена команда --device."
                )
            self.woeusb_check = {
                "ok": True,
                "path": command[0],
            }


def make_handler(engine, token):
    class RequestHandler(BaseHTTPRequestHandler):
        server_version = "USBImageWriter/1.0"

        def log_message(self, format_string, *args):
            request_line = format_string % args
            request_path = request_line.split(" ")[1]
            if request_path.startswith("/api/status?"):
                return
            if request_path.startswith(f"/{token}/"):
                request_line = request_line.replace(request_path, "/<session>/")
            print(f"[web] {self.address_string()} {request_line}")

        def _send(self, status, body, content_type="application/json; charset=utf-8"):
            encoded = body if isinstance(body, bytes) else body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            content_policy = (
                "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                "script-src 'self'; img-src 'self' data:; connect-src 'self'; "
                "base-uri 'none'; frame-ancestors 'none'"
            )
            self.send_header("Content-Security-Policy", content_policy)
            self.end_headers()
            self.wfile.write(encoded)

        def _json(self, status, value):
            self._send(
                status,
                json.dumps(value, ensure_ascii=False).encode("utf-8"),
            )

        def _authorized(self):
            supplied = self.headers.get("X-Session-Token", "")
            if not hmac.compare_digest(supplied, token):
                self._json(403, {"error": "Недействительный токен сессии."})
                return False
            host = self.headers.get("Host", "")
            if host not in {
                f"127.0.0.1:{self.server.server_port}",
                f"localhost:{self.server.server_port}",
            }:
                self._json(403, {"error": "Доступ разрешён только с этого компьютера."})
                return False
            if self.command == "POST":
                origin = self.headers.get("Origin")
                if origin != f"http://{host}":
                    self._json(403, {"error": "Недопустимый источник запроса."})
                    return False
            return True

        def _read_json(self):
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 1024 * 1024:
                    raise ValueError("Некорректный размер запроса.")
                payload = json.loads(self.rfile.read(size))
            except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as error:
                raise ValueError(f"Некорректный JSON: {error}") from error
            if not isinstance(payload, dict):
                raise ValueError("Ожидался JSON-объект.")
            return payload

        def do_GET(self):
            parsed = urllib.parse.urlsplit(self.path)
            if parsed.path == f"/{token}/":
                page = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
                self._send(
                    200,
                    page.replace(
                        "/__SESSION_TOKEN__/assets/app.js",
                        f"/{token}/assets/app.js",
                    ).encode("utf-8"),
                    "text/html; charset=utf-8",
                )
                return
            if parsed.path == "/assets/app.css":
                asset = WEB_ROOT / "app.css"
                self._send(
                    200,
                    asset.read_bytes(),
                    "text/css; charset=utf-8",
                )
                return
            if parsed.path == f"/{token}/assets/app.js":
                self._send(
                    200,
                    (WEB_ROOT / "app.js").read_bytes(),
                    "text/javascript; charset=utf-8",
                )
                return
            if parsed.path == "/api/status":
                if not self._authorized():
                    return
                query = urllib.parse.parse_qs(parsed.query)
                try:
                    after = max(0, int(query.get("after", ["0"])[0]))
                except ValueError:
                    after = 0
                self._json(200, engine.get_state(after))
                return
            self._json(404, {"error": "Страница не найдена."})

        def do_POST(self):
            if not self._authorized():
                return
            parsed = urllib.parse.urlsplit(self.path)
            try:
                if parsed.path == "/api/image/path":
                    payload = self._read_json()
                    engine.set_image_path(payload.get("path", ""))
                    self._json(200, {"image": engine.get_state()["image"]})
                elif parsed.path == "/api/image/upload":
                    name = urllib.parse.unquote(
                        self.headers.get("X-Image-Name", "")
                    )
                    size = int(self.headers.get("Content-Length", "0"))
                    image = engine.store_upload(name, size, self.rfile)
                    self._json(200, {"image": image})
                elif parsed.path == "/api/action":
                    payload = self._read_json()
                    operation = payload.pop("operation", "")
                    engine.start(operation, payload)
                    self._json(202, {"accepted": True})
                else:
                    self._json(404, {"error": "Неизвестный адрес."})
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
                self._json(400, {"error": str(error)})

    return RequestHandler


def main():
    token = secrets.token_urlsafe(32)
    engine = ImageWriterEngine()
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        make_handler(engine, token),
    )
    server.daemon_threads = True
    url = f"http://127.0.0.1:{server.server_port}/{token}/"
    print("USB Image Writer запущен только на этом компьютере.")
    print(f"Открываю: {url}")
    try:
        webbrowser.open(url)
    except webbrowser.Error as error:
        print(f"Не удалось открыть браузер автоматически: {error}")
        print(f"Откройте ссылку вручную: {url}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nЗавершение USB Image Writer…")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
