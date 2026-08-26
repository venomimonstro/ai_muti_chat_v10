import hashlib

from .models import AdminAuditEvent, FeatureFlag


def request_ip(request):
    value = request.META.get("REMOTE_ADDR")
    return value or None


def audit(request, action, target_type, target_id="", metadata=None):
    return AdminAuditEvent.objects.create(
        actor=request.user,
        action=action,
        target_type=target_type,
        target_id=str(target_id),
        metadata=metadata or {},
        request_ip=request_ip(request),
    )


def feature_enabled(key, user=None):
    flag = FeatureFlag.objects.filter(key=key).first()
    if flag is None or not flag.enabled:
        return False
    user_id = str(user.id) if user and user.is_authenticated else "anonymous"
    if user_id in flag.deny_user_ids:
        return False
    if user_id in flag.allow_user_ids:
        return True
    bucket = int(hashlib.sha256(f"{key}:{user_id}".encode()).hexdigest()[:8], 16) % 100
    return bucket < flag.rollout_percent
