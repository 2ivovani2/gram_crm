"""
S3-compatible file storage for all CRM media (screenshots, uploads).

Architecture
───────────
  Dev  → MinIO container (docker-compose.dev.yml, http://minio:9000).
          Bucket policy: public-read. No signed URLs (MEDIA_QUERYSTRING_AUTH=false).
          Django connects via internal Docker hostname; browser needs the public one.
          MEDIA_S3_PUBLIC_URL=http://localhost:9000 rewrites the internal host in URLs.

  Prod → Cloudflare R2 (recommended) / AWS S3 / any S3-compatible service.
          Bucket policy: private. Signed URLs with 1-hour TTL.
          MEDIA_S3_PUBLIC_URL is not set — boto3 URLs point directly to the endpoint.

Both environments use the same backend class. Only env vars differ.

Required env vars (set in .env):
  AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_STORAGE_BUCKET_NAME, AWS_S3_ENDPOINT_URL

Optional env vars:
  AWS_S3_REGION_NAME      default "auto"
  MEDIA_QUERYSTRING_AUTH  default true  (set false in dev — MinIO public bucket)
  MEDIA_QUERYSTRING_EXPIRE default 3600 (signed URL TTL in seconds)
  MEDIA_S3_PUBLIC_URL      default ""   (set in dev to rewrite internal Docker hostname)
"""
from __future__ import annotations

from django.conf import settings
from storages.backends.s3boto3 import S3Boto3Storage


class MediaStorage(S3Boto3Storage):
    """
    Primary storage backend for CRM file uploads.

    Works identically for AWS S3, Cloudflare R2, and MinIO.
    All configuration is read from Django settings (sourced from .env).

    The only custom behaviour is URL rewriting for local development:
    boto3 generates URLs with the internal Docker hostname (minio:9000)
    which is unreachable from the browser. MEDIA_S3_PUBLIC_URL replaces it.
    """

    # Don't send x-amz-acl header — let bucket policy control access.
    # Cloudflare R2 rejects ACL headers entirely; MinIO uses bucket policy;
    # AWS S3 works fine without per-object ACL when bucket policy is set.
    default_acl = None

    def url(self, name: str) -> str:
        raw = super().url(name)

        # Swap internal Docker hostname with the public-facing URL.
        # Only active in dev when MEDIA_S3_PUBLIC_URL is set.
        public = getattr(settings, "MEDIA_S3_PUBLIC_URL", "").rstrip("/")
        endpoint = getattr(settings, "AWS_S3_ENDPOINT_URL", None)
        if public and endpoint and raw.startswith(endpoint.rstrip("/")):
            raw = public + raw[len(endpoint.rstrip("/")):]

        return raw
