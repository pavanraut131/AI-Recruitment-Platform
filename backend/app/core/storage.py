"""Deprecated re-export shim; use :mod:`app.platform.storage`.

The names are bound once, at import: to replace a storage function (e.g. in tests), patch
``app.platform.storage``, which is where the app looks them up, not this module.
"""

# TODO(E0-10): remove
from app.platform.storage import (
    delete_file,
    download_file,
    ensure_bucket_exists,
    get_presigned_url,
    get_s3_client,
    upload_file,
)

__all__ = [
    "delete_file",
    "download_file",
    "ensure_bucket_exists",
    "get_presigned_url",
    "get_s3_client",
    "upload_file",
]
