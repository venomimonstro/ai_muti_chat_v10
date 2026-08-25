import re
import zipfile
from xml.etree import ElementTree

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import FileAsset, FileChunk, FileProcessingJob
from .rag import prepare_chunk


class PartialExtraction(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _normalize(value: str) -> str:
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = "\n".join(line.rstrip() for line in value.splitlines())
    return re.sub(r"\n{3,}", "\n\n", value).strip()


def _read_limited(stream) -> str:
    byte_limit = settings.FILE_MAX_EXTRACTED_CHARS * 4
    payload = stream.read(byte_limit + 1)
    if len(payload) > byte_limit:
        raise PartialExtraction("extracted_text_limit")
    text = payload.decode("utf-8-sig")
    if len(text) > settings.FILE_MAX_EXTRACTED_CHARS:
        raise PartialExtraction("extracted_text_limit")
    return _normalize(text)


def _xml_root(archive, name):
    info = archive.getinfo(name)
    xml_limit = min(
        settings.FILE_MAX_UNCOMPRESSED_BYTES,
        settings.FILE_MAX_EXTRACTED_CHARS * 8,
    )
    if info.file_size > xml_limit:
        raise PartialExtraction("xml_size_limit")
    payload = archive.read(name)
    upper = payload[:4096].upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise PartialExtraction("unsafe_xml")
    return ElementTree.fromstring(payload)


def _docx_sections(stream):
    with zipfile.ZipFile(stream) as archive:
        root = _xml_root(archive, "word/document.xml")
    paragraphs = [
        "".join((node.text or "") for node in paragraph.iter() if node.tag.endswith("}t"))
        for paragraph in root.iter()
        if paragraph.tag.endswith("}p")
    ]
    text = "\n".join(value for value in paragraphs if value)
    return [("document", _normalize(text))]


def _xlsx_sections(stream):
    sections = []
    with zipfile.ZipFile(stream) as archive:
        shared = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = _xml_root(archive, "xl/sharedStrings.xml")
            shared = [
                "".join((node.text or "") for node in item.iter() if node.tag.endswith("}t"))
                for item in root
            ]
        sheet_names = sorted(
            name
            for name in archive.namelist()
            if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")
        )
        for sheet_name in sheet_names:
            root = _xml_root(archive, sheet_name)
            values = []
            for cell in (node for node in root.iter() if node.tag.endswith("}c")):
                value = next((node.text for node in cell if node.tag.endswith("}v")), None)
                if value is None:
                    value = "".join(
                        (node.text or "") for node in cell.iter() if node.tag.endswith("}t")
                    )
                elif cell.attrib.get("t") == "s" and value.isdigit():
                    index = int(value)
                    value = shared[index] if index < len(shared) else ""
                if value:
                    values.append(value)
            sections.append((sheet_name.rsplit("/", 1)[-1], _normalize("\n".join(values))))
            if sum(len(content) for _, content in sections) > settings.FILE_MAX_EXTRACTED_CHARS:
                raise PartialExtraction("extracted_text_limit")
    return sections


def _extract(asset: FileAsset):
    with asset.blob.open("rb") as stream:
        if asset.detected_type in {"text", "csv"}:
            return [("document", _read_limited(stream))]
        if asset.detected_type == "docx":
            return _docx_sections(stream)
        if asset.detected_type == "xlsx":
            return _xlsx_sections(stream)
        if asset.detected_type == "pdf":
            raise PartialExtraction("pdf_extractor_unavailable")
        if asset.detected_type in {"png", "jpeg", "webp"}:
            return []
    raise PartialExtraction("unsupported_extractor")


def _chunks(sections):
    position = 0
    for source, content in sections:
        start = 0
        while start < len(content):
            hard_end = min(start + settings.FILE_CHUNK_CHARS, len(content))
            end = hard_end
            if hard_end < len(content):
                floor = start + int(settings.FILE_CHUNK_CHARS * 0.6)
                boundaries = [
                    content.rfind("\n\n", floor, hard_end),
                    content.rfind("\n", floor, hard_end),
                    content.rfind(". ", floor, hard_end),
                ]
                boundary = max(boundaries)
                if boundary > start:
                    end = boundary + (2 if content[boundary : boundary + 2] == ". " else 0)
            yield FileChunk(
                position=position,
                source_location={"source": source, "start_char": start, "end_char": end},
                content=content[start:end],
            )
            position += 1
            if end == len(content):
                break
            start = max(end - settings.FILE_CHUNK_OVERLAP_CHARS, start + 1)


def process_file(asset: FileAsset):
    job = FileProcessingJob.objects.create(file=asset)
    asset.status = FileAsset.Status.PARSING
    asset.save(update_fields=["status", "updated_at"])
    job.state = FileProcessingJob.State.RUNNING
    job.started_at = timezone.now()
    job.save(update_fields=["state", "started_at"])
    try:
        sections = _extract(asset)
        chars = sum(len(content) for _, content in sections)
        if chars > settings.FILE_MAX_EXTRACTED_CHARS:
            raise PartialExtraction("extracted_text_limit")
        chunks = list(_chunks(sections))
        chunks = [prepare_chunk(chunk, asset) for chunk in chunks]
        with transaction.atomic():
            asset.chunks.all().delete()
            FileChunk.objects.bulk_create(chunks)
            asset.status = FileAsset.Status.READY
            asset.extracted_chars = chars
            asset.error_code = ""
            asset.save(update_fields=["status", "extracted_chars", "error_code", "updated_at"])
            job.state = FileProcessingJob.State.COMPLETED
            job.finished_at = timezone.now()
            job.save(update_fields=["state", "finished_at"])
    except PartialExtraction as exc:
        asset.status = FileAsset.Status.PARTIAL
        asset.error_code = exc.code
        asset.save(update_fields=["status", "error_code", "updated_at"])
        job.state = FileProcessingJob.State.PARTIAL
        job.error_code = exc.code
        job.finished_at = timezone.now()
        job.save(update_fields=["state", "error_code", "finished_at"])
    except Exception:
        asset.status = FileAsset.Status.FAILED
        asset.error_code = "extraction_failed"
        asset.save(update_fields=["status", "error_code", "updated_at"])
        job.state = FileProcessingJob.State.FAILED
        job.error_code = "extraction_failed"
        job.finished_at = timezone.now()
        job.save(update_fields=["state", "error_code", "finished_at"])
    asset.refresh_from_db()
    return asset
