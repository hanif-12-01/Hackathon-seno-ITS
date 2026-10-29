import argparse
import hashlib
import json
from pathlib import Path

FILES = (
    'cabai_rawit_merah_jatim_daily_processed.csv',
    'model_metadata_m7b1.json', 'evaluation_predictions.json',
    'xgb_model_h1.json', 'xgb_model_h3.json', 'xgb_model_h7.json',
)


def provision(source, destination, manifest):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    expected = json.loads(Path(manifest).read_text(encoding='utf-8'))
    if set(expected) != set(FILES) or any(not isinstance(v, str) or len(v) != 64 for v in expected.values()):
        raise ValueError('Manifest must contain exactly six required SHA256 entries')
    repository = Path(__file__).resolve().parent.parent
    if destination == repository or repository in destination.parents:
        raise ValueError('Private runtime destination must be outside repository')
    if destination.exists() or source == destination or source in destination.parents:
        raise ValueError('Destination must be new and outside source')
    payloads = {}
    for name in FILES:
        path = source / name
        if path.is_symlink() or not path.is_file():
            raise ValueError('Missing or linked private asset')
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != expected[name]:
            raise ValueError('Private asset checksum mismatch')
        payloads[name] = data
    destination.mkdir(mode=0o700)
    for name, data in payloads.items():
        with (destination / name).open('xb') as stream:
            stream.write(data)
        (destination / name).chmod(0o600)
        if hashlib.sha256((destination / name).read_bytes()).hexdigest() != expected[name]:
            raise ValueError('Copied asset checksum mismatch; incomplete destination retained')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Copy trusted private inference files only; no training or network access')
    parser.add_argument('--source', required=True)
    parser.add_argument('--destination', required=True)
    parser.add_argument('--manifest', required=True, help='Trusted out-of-band JSON name-to-SHA256 map')
    args = parser.parse_args()
    provision(args.source, args.destination, args.manifest)
