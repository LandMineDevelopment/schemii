"""Cross-product infrastructure failures with safe public meaning."""


class MetadataStorageUnavailableError(RuntimeError):
    """A configured metadata repository cannot currently complete work."""


class MetadataCapacityError(MetadataStorageUnavailableError):
    """The process-local metadata connection allowance is occupied."""
