from io import BytesIO

from PIL import Image, ImageStat, UnidentifiedImageError

from .adapters import ImageProviderError

MAX_PIXELS = 67_108_864
MIN_SIDE = 64
MAX_SIDE = 8192


def validate_generated_image(content: bytes):
    if not content:
        raise ImageProviderError("Empty image payload", code="invalid_image")
    try:
        with Image.open(BytesIO(content)) as probe:
            probe.verify()
        with Image.open(BytesIO(content)) as image:
            width, height = image.size
            if width < MIN_SIDE or height < MIN_SIDE:
                raise ImageProviderError("Image dimensions are too small", code="invalid_image")
            if width > MAX_SIDE or height > MAX_SIDE or width * height > MAX_PIXELS:
                raise ImageProviderError("Image dimensions are unsafe", code="invalid_image")
            sample = image.convert("RGB")
            sample.thumbnail((64, 64))
            stats = ImageStat.Stat(sample)
            means = stats.mean
            variances = stats.var
            nearly_uniform = max(variances) < 1.0
            nearly_black = max(means) < 3.0
            nearly_white = min(means) > 252.0
            if nearly_uniform and (nearly_black or nearly_white):
                raise ImageProviderError("Generated image is effectively blank", code="blank_image")
            return {"width": width, "height": height}
    except ImageProviderError:
        raise
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ImageProviderError("Generated image cannot be decoded", code="invalid_image") from exc
