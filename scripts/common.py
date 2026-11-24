import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / 'state'
DATA = ROOT / 'data'
IMAGE = 'ghcr.io/yunwei37/dgx-spark-4-ring-no-switch@sha256:234754c5b53aa21c29d96afe2c0e9361d81509c34fc0707eef7a1b40f3797579'
NCCL_IMAGE = 'ghcr.io/yunwei37/dgx-spark-4-ring-no-switch@sha256:ee6e0bb0ea8e36a10e3d9ed85745427deb1109e5b2e5ab27f32b0b7c15ed309e'
WEBUI_IMAGE = 'ghcr.io/open-webui/open-webui@sha256:abd02d41e522d41be389d28d1a73752cd2afc4aa46f980b60d490ecc9379c16b'
MODEL_REPO = 'deepseek-ai/DeepSeek-V4.1-Flash-1017'
MODEL_REVISION = '1bae0e1088993b9b82de08f59de5be5a34e42170'
NET_COMMIT = '50ec297c6078e439d7c644cac497bb9445f0041f'
MODEL_NAME = 'deepseek-v4.1-flash-1017'
NAME = 'spark-deepseek-1017'
LABEL = 'local.spark-guide=deepseek-1017'


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def run(args, **kwargs):
    return subprocess.run([str(x) for x in args], check=True, **kwargs)


def output(args):
    return subprocess.check_output([str(x) for x in args], text=True).strip()


def config(name='cluster.json'):
    path = STATE / name
    require(path.is_file(), f'缺少配置：{path}')
    return json.loads(path.read_text())


def private_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open('x', encoding='utf-8') as stream:
        os.chmod(path, 0o600)
        json.dump(data, stream, ensure_ascii=False, indent=2)


def private_env(path, data):
    for value in data.values():
        require('\n' not in str(value) and '\r' not in str(value), '环境变量不能包含换行')
    with path.open('x', encoding='utf-8') as stream:
        os.chmod(path, 0o600)
        stream.write(''.join(f'{key}={value}\n' for key, value in data.items()))
