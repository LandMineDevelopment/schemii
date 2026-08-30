"""Metadata ownership contracts and the current prototype composition."""

from .factory import MetadataRepositories, create_metadata_repositories
from .models import Principal, get_current_principal

__all__ = [
    "MetadataRepositories",
    "Principal",
    "create_metadata_repositories",
    "get_current_principal",
]
