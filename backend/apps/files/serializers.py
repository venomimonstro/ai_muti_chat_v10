from rest_framework import serializers

from .models import FileAsset, FileChunk, FileProcessingJob


class FileChunkSerializer(serializers.ModelSerializer):
    class Meta:
        model = FileChunk
        fields = ("position", "source_location", "content")


class FileJobSerializer(serializers.ModelSerializer):
    class Meta:
        model = FileProcessingJob
        fields = ("id", "state", "attempt", "error_code", "started_at", "finished_at")


class FileAssetSerializer(serializers.ModelSerializer):
    latest_job = serializers.SerializerMethodField()

    class Meta:
        model = FileAsset
        fields = (
            "id",
            "project",
            "original_name",
            "declared_content_type",
            "detected_type",
            "size_bytes",
            "sha256",
            "status",
            "scan_status",
            "error_code",
            "extracted_chars",
            "latest_job",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    def get_latest_job(self, obj):
        job = obj.jobs.first()
        return FileJobSerializer(job).data if job else None
