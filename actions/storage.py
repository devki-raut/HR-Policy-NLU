"""Local/Azure Blob policy-library persistence."""
from __future__ import annotations

import os
from pathlib import Path

from .policy_store import index_path, policy_markdown_dir, policy_pdf_dir

ROOT = Path(__file__).resolve().parents[1]


def enabled() -> bool:
    return os.getenv('STORAGE_BACKEND', 'local').strip().casefold() in {'azure', 'azure_blob', 'blob'}


def _container():
    try:
        from azure.storage.blob import BlobServiceClient
    except ImportError as exc:
        raise RuntimeError('Azure Blob storage requires azure-storage-blob') from exc
    connection = os.getenv('AZURE_STORAGE_CONNECTION_STRING', '').strip()
    account = os.getenv('AZURE_STORAGE_ACCOUNT_NAME', '').strip()
    container = os.getenv('AZURE_STORAGE_CONTAINER_NAME', '').strip()
    if not container:
        raise RuntimeError('AZURE_STORAGE_CONTAINER_NAME is not configured')
    if connection:
        service = BlobServiceClient.from_connection_string(connection)
    elif account:
        try:
            from azure.identity import DefaultAzureCredential
            credential = DefaultAzureCredential()
        except ImportError as exc:
            raise RuntimeError('Managed identity storage requires azure-identity') from exc
        service = BlobServiceClient(
            account_url=f'https://{account}.blob.core.windows.net',
            credential=credential,
        )
    else:
        raise RuntimeError('Configure AZURE_STORAGE_CONNECTION_STRING or AZURE_STORAGE_ACCOUNT_NAME')
    return service.get_container_client(container)


def _prefix():
    return os.getenv('AZURE_STORAGE_PREFIX', 'employeeassist/policies').strip().strip('/')


def _blob_name(path: Path) -> str:
    root = index_path().parent
    return '/'.join(part for part in path.relative_to(root).parts)


def sync_from_cloud() -> None:
    if not enabled():
        return
    container = _container()
    prefix = _prefix()
    blobs = list(container.list_blobs(name_starts_with=prefix + '/'))
    if not blobs:
        return
    root = index_path().parent
    for item in blobs:
        relative = item.name[len(prefix) + 1:]
        target = (root / relative).resolve()
        if not target.is_relative_to(root.resolve()):
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(container.download_blob(item.name).readall())


def sync_to_cloud() -> None:
    if not enabled():
        return
    container = _container()
    prefix = _prefix()
    root = index_path().parent
    files = [index_path(), *policy_pdf_dir().glob('*'), *policy_markdown_dir().glob('*'), *root.glob('documents/*')]
    for path in files:
        if path.is_file():
            container.upload_blob(f'{prefix}/{_blob_name(path)}', path.read_bytes(), overwrite=True)
