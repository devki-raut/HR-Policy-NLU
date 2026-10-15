"""Create a reproducible source release without credentials or runtime stores."""
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
VERSION = '1.0.6'
MODEL = 'models/20261015-182438-symmetric-patch.tar.gz'
TOP_LEVEL = {
    '.dockerignore', '.gitignore', 'Dockerfile', 'Makefile', 'README.md',
    'config.yml', 'domain.yml', 'endpoints.yml', 'requirements.txt',
    'requirements-jupyter.txt', 'setup.py', 'nginx.conf',
}
SOURCE_EXTENSIONS = {'.py', '.md', '.html', '.js', '.css', '.json', '.png', '.sh', '.service', '.example'}


def release_files():
    selected = {ROOT/name for name in TOP_LEVEL if (ROOT/name).is_file()}
    for directory in ['actions', 'teams_app', 'scripts', 'deploy', 'tests', 'data']:
        for path in (ROOT/directory).rglob('*'):
            relative = path.relative_to(ROOT)
            # Filter names BEFORE opening files; do not dereference symlinks.
            if any(part.startswith('.') or part in {'__pycache__', 'artifacts', 'logs', 'results'} for part in relative.parts):
                continue
            if path.name in {'employee_directory.json', 'config.local.json', 'credentials.yml'} or path.is_symlink():
                continue
            if not path.is_file():
                continue
            if directory == 'data':
                permitted = (path.suffix in {'.yml', '.yaml', '.json', '.npz'}
                    or ('policies' in relative.parts and path.suffix in {'.pdf', '.md'})
                    or str(relative) == 'data/Report_15_Oct_26_092938.xls')
            else:
                permitted = path.suffix in SOURCE_EXTENSIONS or str(relative) == f'teams_app/app_package/upload/EmployeeAssist-{VERSION}.zip'
            if permitted:
                selected.add(path)
    model = ROOT/MODEL
    if not model.is_file():
        raise FileNotFoundError(f'Required trained model is missing: {MODEL}')
    selected.add(model)
    return sorted(selected)


def main():
    output = ROOT/'artifacts/releases'
    output.mkdir(parents=True, exist_ok=True)
    target = output/f'EmployeeAssist-{VERSION}-source.zip'
    pending = target.with_suffix('.zip.part')
    hashes = {}
    with zipfile.ZipFile(pending, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in release_files():
            relative = path.relative_to(ROOT).as_posix()
            entry = zipfile.ZipInfo.from_file(path, 'HR-Policy-NLU/'+relative)
            entry.compress_type = zipfile.ZIP_DEFLATED
            digest = hashlib.sha256()
            with path.open('rb') as source, archive.open(entry, 'w') as destination:
                for chunk in iter(lambda: source.read(1024*1024), b''):
                    digest.update(chunk)
                    destination.write(chunk)
            hashes[relative] = digest.hexdigest()
        archive.writestr('HR-Policy-NLU/RELEASE-MANIFEST.json', json.dumps({
            'version': VERSION, 'model': MODEL, 'sha256': hashes,
            'excluded': 'Credentials, environment files, employee directory, runtime databases/logs and virtual environments.',
        }, indent=2)+'\n')
    pending.replace(target)
    digest = hashlib.sha256()
    with target.open('rb') as source:
        for chunk in iter(lambda: source.read(1024*1024), b''):
            digest.update(chunk)
    target.with_suffix('.zip.sha256').write_text(digest.hexdigest()+'  '+target.name+'\n')
    print(json.dumps({'archive': str(target), 'files': len(hashes), 'bytes': target.stat().st_size}, indent=2))


if __name__ == '__main__':
    main()
