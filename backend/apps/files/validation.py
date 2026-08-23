import codecs
import hashlib
import re
import stat
import zipfile
from pathlib import Path, PurePosixPath

from django.conf import settings
from django.core.exceptions import ValidationError

EXTENSIONS = {
    "pdf": {".pdf"},
    "docx": {".docx"},
    "xlsx": {".xlsx"},
    "text": {".txt", ".md"},
    "csv": {".csv"},
    "png": {".png"},
    "jpeg": {".jpg", ".jpeg"},
    "webp": {".webp"},
}
DECLARED_TYPES = {
    "pdf": {"application/pdf"},
    "docx": {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
    "xlsx": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
    "text": {"text/plain", "text/markdown"},
    "csv": {"text/csv", "application/csv", "text/plain"},
    "png": {"image/png"},
    "jpeg": {"image/jpeg"},
    "webp": {"image/webp"},
}
GENERIC_TYPES = {"", "application/octet-stream", "binary/octet-stream"}


def safe_original_name(name: str) -> str:
    value = Path(name.replace("\\", "/")).name
    value = re.sub(r"[\x00-\x1f\x7f]", "", value).strip()
    if not value:
        raise ValidationError("Некорректное имя файла")
    return value[:255]


def _validate_zip(uploaded, names):
    uploaded.seek(0)
    try:
        with zipfile.ZipFile(uploaded) as archive:
            infos = archive.infolist()
            if len(infos) > settings.FILE_MAX_ARCHIVE_ENTRIES:
                raise ValidationError("Слишком много объектов внутри файла")
            if len({info.filename for info in infos}) != len(infos):
                raise ValidationError("Архив содержит повторяющиеся пути")
            total = 0
            for info in infos:
                if "\\" in info.filename or "\x00" in info.filename:
                    raise ValidationError("Небезопасный путь внутри архива")
                path = PurePosixPath(info.filename)
                mode = info.external_attr >> 16
                if (
                    path.is_absolute()
                    or ".." in path.parts
                    or info.flag_bits & 0x1
                    or stat.S_ISLNK(mode)
                ):
                    raise ValidationError("Небезопасная структура архива")
                total += info.file_size
                if (
                    info.compress_size
                    and info.file_size / info.compress_size > settings.FILE_MAX_COMPRESSION_RATIO
                ):
                    raise ValidationError("Подозрительная степень сжатия")
            if total > settings.FILE_MAX_UNCOMPRESSED_BYTES:
                raise ValidationError("Распакованный файл превышает лимит")
            archive_names = set(archive.namelist())
            if not names.issubset(archive_names):
                raise ValidationError("Структура Office-файла повреждена")
            if any(name.lower().endswith("vbaproject.bin") for name in archive_names):
                raise ValidationError("Файлы с макросами не поддерживаются")
    except zipfile.BadZipFile as exc:
        raise ValidationError("Повреждённый Office-файл") from exc
    finally:
        uploaded.seek(0)


def detect_and_validate(uploaded):
    if uploaded.size <= 0:
        raise ValidationError("Пустой файл")
    if uploaded.size > settings.FILE_MAX_UPLOAD_BYTES:
        raise ValidationError("Файл превышает допустимый размер")
    original_name = safe_original_name(uploaded.name)
    suffix = Path(original_name).suffix.lower()
    uploaded.seek(0)
    header = uploaded.read(8192)
    uploaded.seek(0)
    if header.startswith((b"MZ", b"\x7fELF")):
        raise ValidationError("Исполняемые файлы запрещены")
    if header.startswith(b"%PDF-"):
        detected = "pdf"
    elif header.startswith(b"\x89PNG\r\n\x1a\n"):
        detected = "png"
    elif header.startswith(b"\xff\xd8\xff"):
        detected = "jpeg"
    elif header.startswith(b"RIFF") and header[8:12] == b"WEBP":
        detected = "webp"
    elif header.startswith(b"PK"):
        uploaded.seek(0)
        try:
            with zipfile.ZipFile(uploaded) as archive:
                names = set(archive.namelist())
        except zipfile.BadZipFile as exc:
            raise ValidationError("Повреждённый ZIP-контейнер") from exc
        finally:
            uploaded.seek(0)
        if "word/document.xml" in names:
            detected = "docx"
            _validate_zip(uploaded, {"[Content_Types].xml", "word/document.xml"})
        elif "xl/workbook.xml" in names:
            detected = "xlsx"
            _validate_zip(uploaded, {"[Content_Types].xml", "xl/workbook.xml"})
        else:
            raise ValidationError("ZIP-архивы не поддерживаются")
    else:
        try:
            header.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValidationError("Неизвестный или неподдерживаемый тип файла") from exc
        if b"\x00" in header:
            raise ValidationError("Бинарный файл замаскирован под текст")
        detected = "csv" if suffix == ".csv" else "text"
    if suffix not in EXTENSIONS[detected]:
        raise ValidationError("Расширение файла не соответствует содержимому")
    declared = (getattr(uploaded, "content_type", "") or "").lower()
    if declared not in GENERIC_TYPES and declared not in DECLARED_TYPES[detected]:
        raise ValidationError("MIME-тип не соответствует содержимому")
    digest = hashlib.sha256()
    decoder = codecs.getincrementaldecoder("utf-8-sig")() if detected in {"text", "csv"} else None
    try:
        for chunk in uploaded.chunks():
            if decoder:
                if b"\x00" in chunk:
                    raise ValidationError("Бинарный файл замаскирован под текст")
                decoder.decode(chunk)
            digest.update(chunk)
        if decoder:
            decoder.decode(b"", final=True)
    except UnicodeDecodeError as exc:
        raise ValidationError("Текстовый файл должен быть в UTF-8") from exc
    uploaded.seek(0)
    return original_name, detected, digest.hexdigest()
