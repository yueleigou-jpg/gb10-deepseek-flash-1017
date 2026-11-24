import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import socket
import time

from common import (ROOT, STATE, DATA, IMAGE, NCCL_IMAGE, NAME, LABEL,
                    MODEL_NAME, MODEL_REVISION, config, output, run, require, private_json)
from ring_environment import ring_settings


def owned_id(name):
    identity = output(['docker', 'ps', '-aq', '--filter', f'name=^/{name}$'])
    if identity:
        owner = output(['docker', 'inspect', '--format', '{{index .Config.Labels "local.spark-guide"}}', identity])
        require(owner == 'deepseek-1017', f'容器名称已被其他服务使用：{name}')
    return identity


def preflight():
    require(platform.system() == 'Linux' and platform.machine() == 'aarch64', '需要 Linux ARM64 DGX Spark')
    total = os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES')
    require(total >= 115 * 2**30, '需要每台 128GB 型号；当前系统可见内存不足 115GiB')
    require('GB10' in output(['nvidia-smi', '--query-gpu=name', '--format=csv,noheader']), '未识别到 GB10')
    with open(os.devnull, 'w') as stream:
        run(['docker', 'info'], stdout=stream)
    require(Path('/dev/infiniband').is_dir(), '缺少 /dev/infiniband')
    DATA.mkdir(exist_ok=True)
    for directory in ('model', 'cache', 'cache/tmp', 'cache/sglang-native', 'cache/engram-tp4', 'nccl'):
        (DATA / directory).mkdir(parents=True, exist_ok=True)
    print(json.dumps({'hostname': socket.gethostname(), 'memory_GiB': round(total/2**30, 1),
                      'free_disk_GiB': round(shutil.disk_usage(DATA).free/2**30, 1),
                      'kernel': platform.release()}, ensure_ascii=False))


def pull():
    preflight()
    for image in (IMAGE, NCCL_IMAGE):
        run(['docker', 'pull', '--platform', 'linux/arm64', image])
        arch = output(['docker', 'image', 'inspect', '--format', '{{.Architecture}}', image])
        require(arch == 'arm64', '镜像架构必须为 arm64')
    require(not (DATA/'nccl/lib/libnccl.so.2').exists(), 'NCCL 已准备；重复执行请使用 check 检查')
    carrier = output(['docker', 'create', '--entrypoint', '/bin/true', NCCL_IMAGE])
    run(['docker', 'cp', f'{carrier}:/opt/nccl-mesh/.', DATA/'nccl'])
    run(['docker', 'rm', carrier])
    require((DATA/'nccl/lib/libnccl.so.2').is_file(), 'NCCL 伴随镜像缺少动态库')
    pip_path = output(['docker', 'run', '--rm', '--entrypoint', 'python3', IMAGE, '-c',
        'import importlib.util; print(next(iter(importlib.util.find_spec("nvidia.nccl").submodule_search_locations)) + "/lib")'])
    require(pip_path.startswith('/') and '\n' not in pip_path, 'NCCL 安装路径无效')
    private_json(STATE/'runtime.json', {'pip_nccl_lib': pip_path, 'image': IMAGE, 'nccl': NCCL_IMAGE})
    run(['docker', 'run', '--rm', '--gpus', 'all', '--entrypoint', 'python3', IMAGE, '-c',
         'import torch; assert torch.cuda.is_available(); print(torch.cuda.get_device_name(0))'])


def check():
    preflight()
    runtime = config('runtime.json')
    require(runtime['image'] == IMAGE and runtime['nccl'] == NCCL_IMAGE, '镜像记录与教程版本不一致')
    for image in (IMAGE, NCCL_IMAGE):
        output(['docker', 'image', 'inspect', '--format', '{{.Id}}', image])
    library = DATA / 'nccl-ring/libnccl.so.2.30.7'
    require(output(['sha256sum', library]).split()[0] ==
            '768a450b5eb84bf3d1191795350e43c96de75aeba4783ec314d47672fe6e1fc6', 'NCCL 环网动态库摘要不符')
    print('节点镜像与 NVIDIA 设备检查通过')


def prepare():
    require(shutil.disk_usage(DATA).free >= 650 * 2**30 or (DATA/'model/config.json').exists(),
            '首次下载要求数据目录所在分区至少有 650GiB 可用空间')
    run(['docker', 'run', '--rm', '--network', 'host', '--entrypoint', 'python3',
         '-e', 'NODE_RANK=0', '-e', 'SKIP_PREPARE=0', '-e', 'SKIP_VERIFY=0',
         '-e', 'STATE_PATH=/cache/download', '-e', 'TMPDIR=/cache/tmp',
         '-v', f'{DATA}/model:/models/DeepSeek-V4.1-Flash',
         '-v', f'{DATA}/cache:/cache', IMAGE, '/opt/dsv41/boot.py', 'prepare'])
    receipt = json.loads((DATA/'cache/download/verification.json').read_text())
    require(receipt['revision'] == MODEL_REVISION and receipt['status'] == 'verified', '模型版本或完整性校验未通过')
    print('模型下载和完整性校验通过')


def network_env():
    node = config('node.json')
    peer = node['nodes'][1] if node['rank'] == 0 else node['nodes'][0]
    route = json.loads(output(['ip', '-j', 'route', 'get', peer]))
    iface = route[0]['dev']
    require(iface != 'lo', '管理网接口不能为 lo')
    hcas = sorted(p.name for p in Path('/sys/class/infiniband').iterdir())
    expected = {'rocep1s0f0', 'rocep1s0f1', 'roceP2p1s0f0', 'roceP2p1s0f1'}
    require(set(hcas) == expected, 'ConnectX 设备名称与本教程适用条件不符；请检查 DGX OS 与接口配置')
    return {'NODE_RANK': str(node['rank']), 'DIST_INIT_ADDR': node['nodes'][0] + ':29500',
            'COLLECTIVE_ADDR': node['nodes'][0] + ':29501',
            'SERVED_MODEL_NAME': MODEL_NAME, 'NCCL_SOCKET_IFNAME': '=' + iface,
            'GLOO_SOCKET_IFNAME': iface, 'TP_SOCKET_IFNAME': iface,
            'NCCL_IB_HCA': '=' + ','.join(hcas), 'NCCL_IB_DISABLE': '0',
            **ring_settings(),
            'NCCL_NVLS_ENABLE': '0', 'NCCL_IGNORE_CPU_AFFINITY': '1',
            'NCCL_DEBUG': 'INFO', 'NCCL_DEBUG_SUBSYS': 'INIT,ENV,NET',
            'SGLANG_NCCL_SO_PATH': config('runtime.json')['pip_nccl_lib'] + '/libnccl.so.2',
            'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1',
            'TMPDIR': '/cache/tmp', 'CUDA_CACHE_PATH': '/cache/cuda',
            'TORCHINDUCTOR_CACHE_DIR': '/cache/torchinductor', 'TRITON_CACHE_DIR': '/cache/triton',
            'HF_HOME': '/cache/huggingface', 'XDG_CACHE_HOME': '/cache/xdg',
            'READY_TIMEOUT_S': '7200'}


def gpu_args(name):
    runtime = config('runtime.json')
    args = ['docker', 'run', '--name', name, '--label', LABEL, '--pull', 'never',
            '--network', 'host', '--ipc', 'host', '--gpus', 'all',
            '--device', '/dev/infiniband', '--ulimit', 'memlock=-1',
            '--ulimit', 'stack=67108864', '--cap-add', 'IPC_LOCK',
            '--log-opt', 'max-size=50m', '--log-opt', 'max-file=4',
            '-v', f'{DATA}/model:/models/DeepSeek-V4.1-Flash:ro',
            '-v', f'{DATA}/cache:/cache',
            '-v', f'{ROOT}/scripts/sitecustomize.py:/opt/dsv41/adapter/sitecustomize.py:ro',
            '-v', f'{ROOT}/scripts/draft_head_fp8_tp4.py:/opt/dsv41/adapter/draft_head_fp8_tp4.py:ro',
            '-v', f'{DATA}/nccl-ring/libnccl.so.2.30.7:{runtime["pip_nccl_lib"]}/libnccl.so.2:ro',
            '-v', f'{ROOT}/scripts:/guide:ro']
    environment = network_env()
    rank = config('node.json')['rank']
    control_nodes = config('control.json')['addresses']
    require(len(control_nodes) == len(set(control_nodes)) == 4, '需要四个不同的控制网地址')
    local_ip = control_nodes[rank]
    addresses = json.loads(output(['ip', '-j', '-4', 'addr', 'show', 'dev', 'sparkctrl0']))
    require(any(entry['local'] == local_ip for entry in addresses[0]['addr_info']), '集群控制地址不符')
    environment.update(GLOO_SOCKET_IFNAME='sparkctrl0', NCCL_SOCKET_IFNAME='=sparkctrl0',
                       TP_SOCKET_IFNAME='sparkctrl0', SGLANG_HOST_IP=local_ip, HOST_IP=local_ip,
                       VLLM_HOST_IP=local_ip, DIST_INIT_ADDR=control_nodes[0]+':29500',
                       COLLECTIVE_ADDR=control_nodes[0]+':29501')
    for key, value in environment.items():
        args += ['-e', f'{key}={value}']
    return args


def pack():
    rank = config('node.json')['rank']
    run(['docker', 'run', '--rm', '--entrypoint', 'python3',
         '-e', 'TMPDIR=/cache/tmp', '-v', f'{DATA}/model:/models/DeepSeek-V4.1-Flash:ro',
         '-v', f'{DATA}/cache:/cache', IMAGE, '/opt/dsv41/pack_engram.py',
         '--rank', str(rank), '--tp', '4', '--out', '/cache/engram-tp4'])
    for layer in (1, 14):
        path = DATA/f'cache/engram-tp4/engram-l{layer}-r{rank}of4.bin'
        require(path.is_file() and path.stat().st_size > 1024**3, f'Engram 文件缺失：{path}')
    private_json(STATE/'packed.json', {'revision': MODEL_REVISION, 'rank': rank})


def launch():
    check()
    require(not owned_id(NAME), '模型容器已经存在；请先停止整个集群')
    node = config('node.json')
    require(config('packed.json') == {'revision': MODEL_REVISION, 'rank': node['rank']}, 'Engram 版本或节点编号不符')
    args = gpu_args(NAME) + ['-d', '--restart', 'no', '--env-file', str(STATE/'backend.env'),
                            '--entrypoint', '/bin/bash', IMAGE, '/guide/container-start.sh']
    run(args)


def collective():
    require(not owned_id(NAME), '通信测试前必须停止模型集群')
    require(not owned_id(NAME+'-check'), '上一次通信测试容器仍存在')
    run(gpu_args(NAME+'-check') + ['--rm', '--entrypoint', 'timeout', IMAGE,
                                  '180', 'python3', '/guide/collective_check.py'])


def stop():
    for name in (NAME, NAME+'-check'):
        identity = owned_id(name)
        if identity:
            run(['docker', 'stop', '--time', '30', identity])
            logpath = STATE/f'{name}-{time.time_ns()}.log'
            with logpath.open('x') as stream:
                os.chmod(logpath, 0o600)
                run(['docker', 'logs', identity], stdout=stream, stderr=stream)
            run(['docker', 'rm', identity])


def status():
    identity = owned_id(NAME)
    if identity:
        print(output(['docker', 'inspect', '--format', '{{.State.Status}}', identity]))
    else:
        print('absent')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['preflight', 'pull', 'check', 'prepare', 'pack', 'launch', 'collective', 'stop', 'status', 'logs'])
    action = parser.parse_args().action
    if action == 'logs':
        run(['docker', 'logs', '--tail', '150', NAME])
    else:
        globals()[action]()
