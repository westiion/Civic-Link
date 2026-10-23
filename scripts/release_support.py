from pathlib import Path
from urllib.parse import unquote, urlsplit

def require(condition, message):
    if not condition: raise ValueError(message)

def local_path(root, value, base=None):

    root=Path(root).resolve(); value=unquote(str(value))
    require(not urlsplit(value).scheme and not value.startswith(('/', '\\')), 'absolute or URI path is not a package path: '+value)
    p=(Path(base) if base is not None else root)/value
    require(p.resolve().is_relative_to(root), 'path escapes package root: '+value)

    require(all(not x.is_symlink() for x in [p,*p.parents] if x!=root and root in x.parents), 'symbolic package path: '+value)
    return p.resolve()
