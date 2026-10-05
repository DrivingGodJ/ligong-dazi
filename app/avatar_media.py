from __future__ import annotations

import warnings
from io import BytesIO

from fastapi import HTTPException
from PIL import Image, ImageOps, UnidentifiedImageError

from app.activity_media import validate_image_content

AVATAR_INPUT_MAX_BYTES = 3 * 1024 * 1024
AVATAR_STORED_MAX_BYTES = 128 * 1024
AVATAR_MAX_PIXELS = 20_000_000


def normalize_avatar(content: bytes, media_type: str) -> bytes:
    if len(content) > AVATAR_INPUT_MAX_BYTES:
        raise HTTPException(413, "头像上传文件不能超过 3 MB，请先压缩图片。")
    validate_image_content(content, media_type, AVATAR_INPUT_MAX_BYTES)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(content)) as source:
                if source.width * source.height > AVATAR_MAX_PIXELS:
                    raise ValueError("image too large")
                source.seek(0)  # Animated formats become a still avatar.
                source = ImageOps.exif_transpose(source)
                image = ImageOps.fit(source.convert("RGBA"), (512, 512), Image.Resampling.LANCZOS)
                clean = Image.new("RGB", (512, 512), "white")
                clean.paste(image, mask=image.getchannel("A"))
                for quality in (82, 70, 58, 45):
                    output = BytesIO()
                    clean.save(output, format="JPEG", quality=quality, optimize=True)
                    if output.tell() <= AVATAR_STORED_MAX_BYTES:
                        return output.getvalue()
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise HTTPException(
            422, "头像无法读取或尺寸过大，请换一张 JPG、PNG、WebP 或 GIF 图片。"
        ) from exc
    raise HTTPException(422, "头像压缩后仍然过大，请换一张图片。")
