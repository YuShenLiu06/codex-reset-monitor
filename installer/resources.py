"""Read optional bundled resources consistently from directories and zipapps."""
from pathlib import Path
import zipfile
import zipimport
import installer


def read_bundled(name):
    if name not in ('public_app.json','trusted_files.json'):
        raise ValueError('Unknown installer resource')
    loader=installer.__loader__
    if isinstance(loader,zipimport.zipimporter):
        with zipfile.ZipFile(loader.archive) as archive:
            resource='installer/'+name
            try:archive.getinfo(resource)
            except KeyError:return None
            return archive.read(resource)
    resource=Path(installer.__file__).parent/name
    return resource.read_bytes() if resource.is_file() else None
