import argparse
from concurrent.futures import ThreadPoolExecutor
import getpass
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import subprocess
import time
import urllib.request

from common import (ROOT, STATE, IMAGE, WEBUI_IMAGE, NET_COMMIT, MODEL_NAME,
                    config, output, private_env, private_json, require, run)

NETWORK = ROOT/'.work/spark-vllm-docker'


def initialize():
    require(not (STATE/'cluster.json').exists(), '配置已经存在，禁止重新生成密钥或覆盖节点顺序')
    user = getpass.getuser()
    require(re.fullmatch(r'[a-z_][a-z0-9_-]*', user), '本教程要求标准 Linux 用户名称')
    require(ROOT == Path('/home')/user/'spark-deploy', '请将教程解压到 /home/用户名/spark-deploy')
    nodes = [str(ipaddress.IPv4Address(input(f'Spark {i+1} 管理网 IPv4 地址：').strip())) for i in range(4)]
    require(len(set(nodes)) == 4, '四个地址必须互不相同')
    addresses = json.loads(output(['ip', '-j', '-4', 'addr']))
    local = {a['local'] for item in addresses for a in item['addr_info']}
    require(nodes[0] in local, '必须在 Spark 1 上运行，且第一个地址必须属于本机')
    pool = input('高速互连地址池（输入本站未占用的私有 IPv4 CIDR）：').strip()
    network = ipaddress.IPv4Network(pool)
    require(network.is_private and network.prefixlen <= 21, '地址池必须是至少包含八个 /24 的私有 IPv4 网络')
    STATE.mkdir(exist_ok=True, mode=0o700)
    os.chmod(STATE, 0o700)
    private_json(STATE/'cluster.json', {'user': user, 'nodes': nodes, 'pool': str(network)})
    private_env(STATE/'backend.env', {'API_KEY': secrets.token_hex(32)})
    private_json(STATE/'secret.json', {'api_key': (STATE/'backend.env').read_text().strip().split('=', 1)[1]})
    print('配置已经保存。下一步运行 network-preview。')


def network_setup(apply):
    cfg = config()
    require(not os.geteuid() == 0, '请使用普通登录用户执行网络配置')
    NETWORK.parent.mkdir(exist_ok=True)
    temporary = ROOT/'.work/tmp'
    temporary.mkdir(exist_ok=True)
    if not NETWORK.exists():
        run(['git', 'clone', '--no-checkout', 'https://github.com/eugr/spark-vllm-docker.git', NETWORK])
        run(['git', '-C', NETWORK, 'checkout', '--detach', NET_COMMIT])
    require(output(['git', '-C', NETWORK, 'rev-parse', 'HEAD']) == NET_COMMIT, '网络脚本版本与教程不符')
    args = ['bash', NETWORK/'setup-cluster.sh', *cfg['nodes'], '--topology', 'ring',
            '--mtu', '1500', '--subnet-pool', cfg['pool']]
    if not apply:
        args += ['--dry-run']
    run(args, env=dict(os.environ, TMPDIR=str(temporary)))


def remote(rank, action, capture=False):
    cfg = config()
    command = ['python3', f'/home/{cfg["user"]}/spark-deploy/scripts/node.py', action]
    args = command if rank == 0 else ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
        '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=3',
        f'{cfg["user"]}@{cfg["nodes"][rank]}', shlex.join(command)]
    return output(args) if capture else run(args)


def distribute():
    cfg = config()
    for rank, host in enumerate(cfg['nodes']):
        local = STATE/f'node-{rank}.json'
        private_json(local, {'rank': rank, 'nodes': cfg['nodes']})
        if rank == 0:
            run(['install', '-m', '600', local, STATE/'node.json'])
            continue
        destination = f'/home/{cfg["user"]}/spark-deploy'
        target = f'{cfg["user"]}@{host}'
        run(['ssh', '-o', 'BatchMode=yes', target,
             shlex.join(['mkdir', '-p', '-m', '700', destination+'/state', destination+'/scripts'])])
        files = sorted((ROOT/'scripts').glob('*.py')) + sorted((ROOT/'scripts').glob('*.sh'))
        run(['scp', '-p', *files, f'{target}:{destination}/scripts/'])
        run(['scp', '-p', STATE/'backend.env', f'{target}:{destination}/state/backend.env'])
        run(['scp', '-p', local, f'{target}:{destination}/state/node.json'])
    print('四台节点配置与脚本已经分发')


def each(action):
    for rank in range(4):
        print(f'===== Spark {rank+1}：{action} =====', flush=True)
        remote(rank, action)


def collective():
    print('同时在四台设备运行真实 GPU AllReduce 和 AllGather 校验', flush=True)
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(remote, rank, 'collective') for rank in range(4)]
        results = [future.result() for future in futures]
    require(len(results) == 4, '四台通信测试结果不完整')
    print('四台通信正确性测试通过；请同时检查输出中的 Mesh 和 IB 设备选择。')


def health():
    response = subprocess.run(['curl', '--silent', '--show-error', '--max-time', '10',
        '--header', '@-',
        '--output', os.devnull, '--write-out', '%{http_code}',
        'http://127.0.0.1:8000/health'], text=True, capture_output=True,
        input='Authorization: Bearer '+config('secret.json')['api_key']+'\n')
    return response.returncode == 0 and response.stdout == '200'


def api(path, payload=None, timeout=600):
    key = config('secret.json')['api_key']
    request = urllib.request.Request('http://127.0.0.1:8000'+path,
        data=None if payload is None else json.dumps(payload).encode(),
        headers={'Content-Type': 'application/json', 'Authorization': 'Bearer '+key})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def completion(prompt, **kwargs):
    return api('/v1/chat/completions', {'model': MODEL_NAME, 'temperature': 0,
        'max_tokens': 256, 'chat_template_kwargs': {'thinking': False},
        'messages': [{'role': 'user', 'content': prompt}], **kwargs})


def verify():
    require(health(), 'API 健康检查未通过')
    models = api('/v1/models')
    require(MODEL_NAME in [m['id'] for m in models['data']], '模型名称不符')
    for rank in range(4):
        require(remote(rank, 'status', True) == 'running', f'Spark {rank+1} 容器未运行')
    unauthorized = subprocess.run(['curl', '-sS', '--max-time', '10', '-o', os.devnull,
        '-w', '%{http_code}', 'http://127.0.0.1:8000/v1/models'], text=True, capture_output=True, check=True)
    require(unauthorized.stdout in ('401', '403'), '模型接口未拒绝无密钥访问')
    prompts = [('19 + 23', 42), ('17 + 25', 42), ('9 * 7', 63), ('100 - 37', 63)]
    def ask(item):
        expression, expected = item
        result = completion(f'Calculate {expression}. Return only the integer, with no explanation.')
        choice = result['choices'][0]
        require(choice['message']['content'].strip() == str(expected), f'实际推理结果不符：{expression}')
        require(choice['finish_reason'] == 'stop', '回答被截断')
        return result['usage']
    with ThreadPoolExecutor(max_workers=4) as pool:
        usages = list(pool.map(ask, prompts))
    structured = completion('Return a JSON object with answer equal to the integer 42.',
        response_format={'type': 'json_schema', 'json_schema': {'name': 'answer', 'strict': True,
            'schema': {'type': 'object', 'properties': {'answer': {'type': 'integer'}},
                       'required': ['answer'], 'additionalProperties': False}}})
    require(json.loads(structured['choices'][0]['message']['content']) == {'answer': 42}, '结构化输出校验失败')
    report = {'time': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'model': MODEL_NAME,
              'checks': ['four_ranks', 'authentication', 'four_concurrent_requests', 'json_schema'], 'usage': usages}
    name = STATE/f'acceptance-{time.time_ns()}.json'
    private_json(name, report)
    print(f'真实 API 验收通过，记录：{name}', flush=True)


def longtest():
    expected = {f'key_{position}': secrets.token_hex(12) for position in (800, 4000, 7200)}
    lines = [f'record_{i}: {secrets.token_hex(16)}' for i in range(8000)]
    for position in (800, 4000, 7200):
        lines[position] = f'key_{position}: {expected[f"key_{position}"]}'
    prompt = '\n'.join(lines) + '\nReturn a JSON object containing the exact values of key_800, key_4000 and key_7200. No Markdown.'
    started = time.monotonic()
    result = completion(prompt, response_format={'type': 'json_object'})
    actual = json.loads(result['choices'][0]['message']['content'])
    require(actual == expected, '长文本检索结果与原始内容不一致')
    require(result['choices'][0]['finish_reason'] == 'stop', '长文本回答被截断')
    report = {'elapsed_seconds': time.monotonic()-started, 'usage': result['usage'], 'matched': True}
    private_json(STATE/f'longtest-{time.time_ns()}.json', report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print('仅声明上方 usage 显示的实际输入长度通过；该测试不代表完整 1M 上下文验收。')


def serve():
    stop()
    for rank in range(4):
        require(remote(rank, 'status', True) == 'absent', '启动前存在模型容器；请先停止整个集群')
    for rank in (1, 2, 3, 0):
        remote(rank, 'launch')
    deadline = time.monotonic() + 7200
    while not health():
        for rank in range(4):
            require(remote(rank, 'status', True) == 'running', f'Spark {rank+1} 启动进程退出，请查看该节点日志')
        require(time.monotonic() < deadline, '模型启动超过 7200 秒，请检查日志')
        time.sleep(10)
    verify()
    run(['systemd-notify', '--ready', '--status=Four ranks and authenticated inference verified'])
    last_healthy = time.monotonic()
    while True:
        time.sleep(15)
        for rank in range(4):
            require(remote(rank, 'status', True) == 'running', f'Spark {rank+1} 运行进程退出')
        if health():
            last_healthy = time.monotonic()
        else:
            elapsed = time.monotonic() - last_healthy
            print(f'API 健康检查暂未通过，距离上次成功 {elapsed:.0f} 秒', flush=True)
            require(elapsed < 180, 'API 持续 180 秒未通过健康检查')


def stop():
    cfg = config()
    results = []
    for rank, host in enumerate(cfg['nodes']):
        command = ['python3', str(ROOT/'scripts/node.py'), 'stop']
        args = command if rank == 0 else ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
            '-o', 'ServerAliveInterval=10', '-o', 'ServerAliveCountMax=2',
            f'{cfg["user"]}@{host}', shlex.join(command)]
        result = subprocess.run(args)
        results.append((rank, result.returncode))
    require(all(code == 0 for _, code in results), f'部分节点停止失败，必须恢复连接后再次停止：{results}')


def ui():
    require(health(), '先完成模型服务启动与验收')
    require(not output(['docker', 'ps', '-aq', '--filter', 'name=^/spark-open-webui$']), 'WebUI 容器已经存在')
    envpath = STATE/'webui.env'
    require(not envpath.exists(), 'WebUI 配置已存在，请保留原密钥并按教程恢复容器')
    email = input('WebUI 管理员邮箱：').strip()
    require('@' in email and '\n' not in email, '请输入有效邮箱')
    password = getpass.getpass('WebUI 管理员密码（至少 12 个字符）：')
    require(len(password) >= 12 and password == getpass.getpass('再次输入密码：'), '密码长度不足或两次输入不一致')
    private_env(envpath, {'OPENAI_API_BASE_URL': 'http://127.0.0.1:8000/v1',
        'OPENAI_API_KEY': config('secret.json')['api_key'], 'ENABLE_OLLAMA_API': 'false',
        'ENABLE_SIGNUP': 'false', 'WEBUI_ADMIN_EMAIL': email, 'WEBUI_ADMIN_PASSWORD': password,
        'WEBUI_SECRET_KEY': secrets.token_hex(32), 'PORT': '3000',
        'ENABLE_PERSISTENT_CONFIG': 'true'})
    run(['docker', 'pull', WEBUI_IMAGE])
    run(['docker', 'run', '-d', '--name', 'spark-open-webui', '--network', 'host',
        '--restart', 'unless-stopped', '--memory', '2g', '--log-opt', 'max-size=25m',
        '--log-opt', 'max-file=3', '--env-file', envpath,
        '-v', 'spark-open-webui:/app/backend/data', WEBUI_IMAGE])
    print(f'聊天页面：http://{config()["nodes"][0]}:3000；管理员使用刚才设置的账户登录。')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='四台 Spark Flash-1017 部署助手')
    parser.add_argument('action', choices=['init', 'network-preview', 'network-apply', 'distribute',
        'preflight', 'pull', 'check', 'prepare', 'pack', 'collective', 'serve', 'stop',
        'status', 'logs', 'verify', 'longtest', 'ui'])
    action = parser.parse_args().action
    if action == 'init':
        initialize()
    elif action.startswith('network-'):
        network_setup(action == 'network-apply')
    elif action in ('preflight', 'pull', 'check', 'prepare', 'pack', 'status', 'logs'):
        each(action)
    else:
        globals()[action]()
