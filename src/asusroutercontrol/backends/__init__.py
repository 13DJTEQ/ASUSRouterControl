"""Firmware backend package.

Public surface::

    from asusroutercontrol.backends import BackendOperationUnsupported
    from asusroutercontrol.backends.factory import create_backend, UnknownBackendError
"""

from asusroutercontrol.backends.base import BackendOperationUnsupported, FirmwareBackend

__all__ = ["BackendOperationUnsupported", "FirmwareBackend"]
