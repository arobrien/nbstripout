from ._nbstripout import main, __doc__ as docstring
from ._installer import install, uninstall, status
from ._utils import pop_recursive, strip_output, StripArgs, MetadataError
__all__ = ["install", "uninstall", "status", "main",
           "pop_recursive", "strip_output", "StripArgs", "MetadataError"]
__doc__ = docstring
