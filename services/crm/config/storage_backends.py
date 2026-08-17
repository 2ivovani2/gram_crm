"""
S3-compatible file storage for all CRM media (screenshots, uploads).

Architecture
───────────
  Dev  → MinIO container (root compose.yaml, http://minio:9000).
          Bucket policy: public-read. No signed URLs (MEDIA_QUERYSTRING_AUTH=false).
          Django connects via internal Docker hostname; browser needs the public one.
          MEDIA_S3_PUBLIC_URL=http://localhost:9000 rewrites the internal host in URLs.

  Prod → private S3-compatible storage. Objects use short-lived signed URLs.
          In Kubernetes, https://media.gramly.tech is the S3 API endpoint and
          Gateway API forwards only that hostname to the private MinIO service.

Both environments use the same backend class. Only env vars differ.

Required env vars (set in .env):
  AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_STORAGE_BUCKET_NAME
  AWS_S3_ENDPOINT_URL        dev: http://minio:9000  prod: https://media.gramly.tech
  MEDIA_S3_PUBLIC_URL        dev: http://localhost:9000  prod: empty
  MEDIA_QUERYSTRING_AUTH     dev: false  prod: true
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
        # Active in both dev and prod when MEDIA_S3_PUBLIC_URL is set.
        public = getattr(settings, "MEDIA_S3_PUBLIC_URL", "").rstrip("/")
        endpoint = getattr(settings, "AWS_S3_ENDPOINT_URL", None)
        if public and endpoint and raw.startswith(endpoint.rstrip("/")):
            raw = public + raw[len(endpoint.rstrip("/")):]

        return raw
