"""Render an isolated nginx test proxy without changing system configuration."""
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def render(output, runtime, web_port=8610, proxy_port=18610):
    if not 0 < web_port < 65536 or not 0 < proxy_port < 65536 or web_port == proxy_port:
        raise ValueError('Use different valid web and proxy ports.')
    runtime = Path(runtime).resolve()
    runtime.mkdir(parents=True, exist_ok=True)
    path = str(runtime)
    if any(char in path for char in ['"', '\\', '\n', '\r']):
        raise ValueError('The nginx runtime path contains unsupported characters.')
    template = (ROOT/'deploy/nginx-subroute.conf.example').read_text()
    content = template.replace('__RUNTIME_DIR__', path).replace('__PROXY_PORT__', str(proxy_port)).replace('__WEB_PORT__', str(web_port))
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    Path(output).write_text(content)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--web-port', type=int, default=8610)
    parser.add_argument('--proxy-port', type=int, default=18610)
    args = parser.parse_args()
    render(args.output, args.runtime, args.web_port, args.proxy_port)
