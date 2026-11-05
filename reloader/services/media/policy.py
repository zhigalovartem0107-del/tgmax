"""Строгое правило для медиа: файл больше лимита — пост пропускается целиком (без текста, ссылок и кнопок)."""

MB = 1024 * 1024
MAX_MEDIA_MB = 250                    # потолок: больше не выкладываем ни при каких настройках
MAX_MEDIA_BYTES = MAX_MEDIA_MB * MB


def size_skip_reason(size, limit_mb=MAX_MEDIA_MB):
    """Причина пропуска поста из-за размера файла или None. Лимит не выше MAX_MEDIA_MB."""
    limit_mb = min(int(limit_mb or MAX_MEDIA_MB), MAX_MEDIA_MB)
    if int(size or 0) > limit_mb * MB:
        return f"Skipped: file >{limit_mb}MB"
    return None
