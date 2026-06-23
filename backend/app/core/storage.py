import boto3
from botocore.client import Config
from botocore.exceptions import ClientError
import structlog
from app.core.config import settings

logger = structlog.get_logger()


def get_s3_client():
    """Return a boto3 S3 client configured for MinIO."""
    return boto3.client(
        "s3",
        endpoint_url=f"{'https' if settings.MINIO_USE_SSL else 'http'}://{settings.MINIO_ENDPOINT}",
        aws_access_key_id=settings.MINIO_ACCESS_KEY,
        aws_secret_access_key=settings.MINIO_SECRET_KEY,
        config=Config(signature_version="s3v4"),
        region_name="us-east-1",
    )


def ensure_bucket_exists():
    """Create the bucket if it doesn't exist."""
    client = get_s3_client()
    try:
        client.head_bucket(Bucket=settings.MINIO_BUCKET_NAME)
        logger.info("bucket_exists", bucket=settings.MINIO_BUCKET_NAME)
    except ClientError as e:
        error_code = e.response["Error"]["Code"]
        if error_code in ("404", "NoSuchBucket"):
            client.create_bucket(Bucket=settings.MINIO_BUCKET_NAME)
            logger.info("bucket_created", bucket=settings.MINIO_BUCKET_NAME)
        else:
            raise


def upload_file(file_bytes: bytes, object_key: str, content_type: str = "application/pdf") -> str:
    """Upload bytes to object storage. Returns the object key."""
    client = get_s3_client()
    client.put_object(
        Bucket=settings.MINIO_BUCKET_NAME,
        Key=object_key,
        Body=file_bytes,
        ContentType=content_type,
    )
    logger.info("file_uploaded", key=object_key, size=len(file_bytes))
    return object_key


def download_file(object_key: str) -> bytes:
    """Download bytes from object storage."""
    client = get_s3_client()
    response = client.get_object(Bucket=settings.MINIO_BUCKET_NAME, Key=object_key)
    return response["Body"].read()


def delete_file(object_key: str) -> None:
    """Delete an object from storage."""
    client = get_s3_client()
    client.delete_object(Bucket=settings.MINIO_BUCKET_NAME, Key=object_key)
    logger.info("file_deleted", key=object_key)


def get_presigned_url(object_key: str, expires_in: int = 3600) -> str:
    """Generate a pre-signed URL for temporary file access."""
    client = get_s3_client()
    return client.generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.MINIO_BUCKET_NAME, "Key": object_key},
        ExpiresIn=expires_in,
    )
