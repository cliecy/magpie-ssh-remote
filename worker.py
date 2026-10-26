#!/usr/bin/env python3
"""Local Magpie plugin helper. Only the plugin's options are used as its UI mailbox."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import signal
import subprocess
import sys
import time
import urllib.request

MARKER = '# managed-by: magpie-ssh-remote v1'
HOST = re.compile(r'^[A-Za-z0-9_][A-Za-z0-9_.@:\-]*$')
IDENT = re.compile(r'^[a-f0-9]{16}$')
AGENT_ID = re.compile(r'^[a-z][a-z0-9._-]{0,79}$')


def native_script(action, profile, agent=None):
    """Delegate wiring and restoration to the server's own Magpie adapters."""
    prefix = ('set -eu\n'
        'mp=$(command -v magpie || true)\n'
        'if [ -z "$mp" ] && [ -x "$HOME/.local/bin/magpie" ]; then mp="$HOME/.local/bin/magpie"; fi\n'
        'if [ -z "$mp" ] && [ -x /Applications/magpie.app/Contents/MacOS/magpie ]; then mp=/Applications/magpie.app/Contents/MacOS/magpie; fi\n'
        'if [ -z "$mp" ]; then echo "服务器未安装 Magpie。请先按官方安装说明安装，再重试。" >&2; exit 1; fi\n')
    provider = 'ssh-desktop-' + profile['id']
    if action == 'native-inspect':
        return prefix + '"$mp" --version\n"$mp" agents\n'
    if action == 'native-setup':
        address = 'url=http://127.0.0.1:%s' % profile['remotePort']
        return prefix + ('if ! "$mp" healthcheck; then\n'
            '  "$mp" daemon install || { echo "无法安装服务；请先在服务器运行 magpie serve，再重试。" >&2; exit 1; }\n'
            '  "$mp" daemon start\nfi\n'
            'if "$mp" provider ' + shlex.quote(provider) + ' >/dev/null 2>&1; then\n'
            '  "$mp" provider set ' + shlex.join([provider, address, 'key=magpie-ssh']) + '\n'
            'else\n  "$mp" provider add ' + shlex.join(['remote-magpie', 'magpie-ssh', address, 'id=' + provider]) + '\nfi\n'
            '"$mp" provider refresh ' + shlex.quote(provider) + '\n'
            '"$mp" healthcheck\n')
    if action not in ('native-apply', 'native-restore'):
        raise ValueError('未知 Magpie 操作')
    if not isinstance(agent, str) or not AGENT_ID.fullmatch(agent) or agent in ('provider', 'plugin', 'daemon', 'serve', 'web', 'update', 'restore', 'rm'):
        raise ValueError('请填写有效的 Magpie agent ID，例如 omp、pi、dsh、codex')
    value = 'default' if action == 'native-restore' else provider + '/' + profile['model']
    # Magpie's help is side-effect free. Require an agent's model field rather
    # than treating an arbitrary top-level Magpie command as an agent ID.
    check = ('"$mp" ' + shlex.quote(agent) + ' --help | grep -Eq \'^fields:.*model\' || '
             '{ echo "该 ID 不是支持 model 字段的 Magpie agent；请查看本体帮助。" >&2; exit 1; }\n')
    return prefix + check + '"$mp" ' + shlex.join([agent, value]) + '\n'


def atomic_json(path, data):
    temporary = path.with_suffix('.tmp')
    fd = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(data, stream, ensure_ascii=False)
    os.replace(str(temporary), str(path))


def validate_profile(value):
    if not isinstance(value, dict):
        raise ValueError('服务器配置格式错误')
    p = {k: value.get(k) for k in ('id', 'name', 'host', 'localPort', 'remotePort', 'model')}
    if not isinstance(p['host'], str) or not HOST.fullmatch(p['host']) or len(p['host']) > 200:
        raise ValueError('SSH 主机名无效；请使用 SSH 配置中的别名或 user@host')
    if not isinstance(p['id'], str) or not IDENT.fullmatch(p['id']):
        raise ValueError('配置 ID 无效')
    for field in ('localPort', 'remotePort'):
        if type(p[field]) is not int or not 1 <= p[field] <= 65535:
            raise ValueError('端口应为 1–65535 的整数')
    if not isinstance(p['model'], str) or not p['model'] or len(p['model']) > 200:
        raise ValueError('请选择完整模型 ID')
    p['name'] = str(p['name'] or p['host'])[:80]
    p['enabled'] = bool(value.get('enabled', False))
    return p


def ssh_args(profile, control):
    return ['ssh', '-S', str(control), '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
            '-o', 'ConnectTimeout=10', '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=2']


def wrapper_content(agent, profile):
    base = 'http://127.0.0.1:%s' % profile['remotePort']
    if agent == 'codex':
        command = ['env', 'MAGPIE_API_KEY=magpie-ssh-codex', 'codex', '-m', profile['model']]
        overrides = {
            'model_provider': 'magpie_ssh',
            'model_providers.magpie_ssh.name': 'Magpie via SSH',
            'model_providers.magpie_ssh.base_url': base + '/v1',
            'model_providers.magpie_ssh.env_key': 'MAGPIE_API_KEY',
            'model_providers.magpie_ssh.wire_api': 'responses',
            'model_providers.magpie_ssh.requires_openai_auth': False,
        }
        for key, value in overrides.items():
            command += ['-c', key + '=' + json.dumps(value)]
    elif agent == 'claude':
        command = ['env', '-u', 'CLAUDE_CODE_OAUTH_TOKEN', '-u', 'ANTHROPIC_API_KEY',
                   '-u', 'CLAUDE_CODE_USE_BEDROCK', '-u', 'CLAUDE_CODE_USE_VERTEX',
                   '-u', 'CLAUDE_CODE_USE_FOUNDRY',
                   'ANTHROPIC_BASE_URL=' + base, 'ANTHROPIC_AUTH_TOKEN=magpie-ssh-claude',
                   'claude', '--model', profile['model']]
    else:
        raise ValueError('请选择 Codex 或 Claude Code')
    return '#!/bin/sh\n' + MARKER + '\nexec ' + shlex.join(command) + ' "$@"\n'


def wrapper_script(agent, profile, remove=False):
    content = wrapper_content(agent, profile)
    target = '"$HOME/.local/bin/' + agent + '-magpie"'
    guard = ('if [ -e ' + target + ' ] || [ -L ' + target + ' ]; then\n'
             '  if [ -L ' + target + ' ] || ! grep -qxF ' + shlex.quote(MARKER) + ' ' + target + '; then\n'
             '    echo "拒绝覆盖或删除：已有同名文件不属于本插件" >&2; exit 1;\n  fi\nfi\n')
    if remove:
        return 'set -eu\n' + guard + 'rm -f -- ' + target + '\necho "启动命令已移除"\n'
    return ('set -eu\numask 077\nmkdir -p "$HOME/.local/bin"\n' + guard +
            'temp=$(mktemp "$HOME/.local/bin/.magpie-ssh.XXXXXX")\n'
            'trap \'rm -f "$temp"\' EXIT HUP INT TERM\n'
            'printf %s ' + shlex.quote(content) + ' > "$temp"\nchmod 700 "$temp"\n'
            'mv -f -- "$temp" ' + target + '\necho "已安装 ~/.local/bin/' + agent + '-magpie"\n')


class Manager:
    def __init__(self, config, plugin, magpie):
        self.config, self.plugin, self.magpie = Path(config), str(plugin), magpie
        self.runtime = self.config / 'ssh-remote'
        self.runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.state_file = self.runtime / 'state.json'
        self.state = json.loads(self.state_file.read_text()) if self.state_file.exists() else {'profiles': []}
        self.state['profiles'] = [validate_profile(p) for p in self.state['profiles']]
        self.processes, self.statuses, self.retry, self.handles = {}, {}, {}, {}
        self.native_reports = {}
        # Keep Unix-domain socket paths below macOS's length limit.
        digest = hashlib.sha256(str(self.config).encode()).hexdigest()[:10]
        self.sockets = Path('/tmp') / ('magpie-ssh-%s-%s' % (os.getuid(), digest))
        self.sockets.mkdir(mode=0o700, exist_ok=True)
        info = self.sockets.lstat()
        if self.sockets.is_symlink() or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise RuntimeError('SSH 控制目录权限不安全')

    def entry(self):
        data = json.loads((self.config / 'plugins.json').read_text())
        return next((p for p in data.get('plugins', []) if p.get('spec') == self.plugin), None)

    def save(self):
        atomic_json(self.state_file, self.state)

    def profile(self, identity):
        value = next((p for p in self.state['profiles'] if p['id'] == identity), None)
        if value is None:
            raise ValueError('服务器配置不存在，请刷新后重试')
        return value

    def socket(self, p):
        return self.sockets / p['id']

    def local_models(self, local_port=3425):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        request = urllib.request.Request('http://127.0.0.1:%s/v1/models' % local_port,
                                         headers={'Authorization': 'Bearer magpie-ssh'})
        with opener.open(request, timeout=5) as response:
            catalog = json.load(response)
        return [m['id'] for m in catalog['data']]

    def ssh(self, p, command=None, input_text=None, timeout=25):
        argv = ssh_args(p, self.socket(p)) + [p['host']]
        if command is not None:
            argv += [command]
        result = subprocess.run(argv, input=input_text, capture_output=True, text=True, timeout=timeout)
        if result.returncode:
            raise RuntimeError((result.stderr.strip() or result.stdout.strip() or 'SSH 操作失败')[-1500:])
        return result.stdout

    def stop(self, p):
        proc = self.processes.pop(p['id'], None)
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=4)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        if self.socket(p).exists():
            subprocess.run(ssh_args(p, self.socket(p)) + ['-O', 'exit', p['host']],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=12)
        handle = self.handles.pop(p['id'], None)
        if handle:
            handle.close()
        self.retry.pop(p['id'], None)
        self.statuses[p['id']] = {'state': 'disconnected', 'message': '已断开'}

    def start(self, p):
        if p['id'] in self.processes and self.processes[p['id']].poll() is None:
            return
        self.stop(p)
        self.local_models(p['localPort'])
        logfile = self.runtime / (p['id'] + '.log')
        log = open(logfile, 'w')
        os.chmod(str(logfile), 0o600)
        self.handles[p['id']] = log
        argv = ssh_args(p, self.socket(p)) + ['-M', '-NT', '-o', 'ControlPersist=no',
                '-o', 'ExitOnForwardFailure=yes', '-R', '127.0.0.1:%s:127.0.0.1:%s' %
                (p['remotePort'], p['localPort']), p['host']]
        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=log)
        self.processes[p['id']] = proc
        self.statuses[p['id']] = {'state': 'connecting', 'message': '正在连接', 'started': time.time()}

    def probe(self, p):
        command = shlex.join(['curl', '--noproxy', '*', '-fsS', '--max-time', '8',
                '-H', 'Authorization: Bearer magpie-ssh',
                'http://127.0.0.1:%s/v1/models' % p['remotePort']])
        value = json.loads(self.ssh(p, command))
        if not isinstance(value.get('data'), list):
            raise RuntimeError('远端没有返回模型列表')
        return len(value['data'])

    def monitor(self):
        for p in self.state['profiles']:
            if not p.get('enabled'):
                continue
            identity = p['id']
            proc = self.processes.get(identity)
            status = self.statuses.get(identity, {})
            try:
                if proc is not None and proc.poll() is not None:
                    logfile = self.runtime / (identity + '.log')
                    error = logfile.read_text(errors='replace')[-1200:].strip() or 'SSH 连接已结束'
                    self.stop(p)
                    raise RuntimeError(error)
                if proc is None and time.time() >= self.retry.get(identity, 0):
                    self.start(p)
                elif proc is not None and status.get('state') == 'connecting':
                    checked = subprocess.run(ssh_args(p, self.socket(p)) + ['-O', 'check', p['host']],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
                    if checked.returncode == 0:
                        count = self.probe(p)
                        self.statuses[identity] = {'state': 'connected', 'message': '已连接 · %s 个模型' % count}
                    elif time.time() - status.get('started', 0) > 35:
                        raise RuntimeError('连接超时；请先在终端 ssh 到该主机，确认密钥和主机指纹')
            except Exception as error:
                self.stop(p)
                self.statuses[identity] = {'state': 'retrying', 'message': str(error)}
                self.retry[identity] = time.time() + 15

    def snapshot(self):
        hosts = []
        sshconfig = Path.home() / '.ssh/config'
        if sshconfig.exists():
            for line in sshconfig.read_text().splitlines():
                parts = line.strip().split()
                if parts and parts[0].lower() == 'host':
                    hosts += [h for h in parts[1:] if HOST.fullmatch(h) and '*' not in h and '?' not in h]
        try:
            models, model_error = self.local_models(), ''
        except Exception as error:
            models, model_error = [], str(error)
        return {'profiles': self.state['profiles'], 'statuses': self.statuses,
                'helperVersion': '0.2.0',
                'nativeReports': self.native_reports,
                'hosts': list(dict.fromkeys(hosts)), 'models': models, 'modelError': model_error,
                'helperPid': os.getpid(), 'at': time.time()}

    def handle(self, request):
        action = request.get('action')
        if action == 'snapshot':
            return '状态已刷新'
        if action == 'save':
            p = validate_profile(request.get('profile'))
            # Server/port edits explicitly disconnect an existing tunnel first.
            existing = next((x for x in self.state['profiles'] if x['id'] == p['id']), None)
            if existing:
                self.stop(existing)
                self.state['profiles'].remove(existing)
            p['enabled'] = False
            self.state['profiles'].append(p)
            self.save()
            return '服务器已保存；点击连接后启用'
        p = self.profile(request.get('profileId'))
        if action == 'connect':
            for other in self.state['profiles']:
                if other['id'] != p['id'] and other.get('enabled') and other['host'] == p['host'] and other['remotePort'] == p['remotePort']:
                    raise ValueError('同一主机的远端端口已被另一配置使用')
            p['enabled'] = True
            self.save()
            self.start(p)
            return '正在连接；稍后刷新状态可查看结果'
        if action == 'disconnect':
            p['enabled'] = False
            self.save()
            self.stop(p)
            return '已断开并关闭自动重连'
        if action == 'delete':
            self.stop(p)
            self.state['profiles'].remove(p)
            self.statuses.pop(p['id'], None)
            self.native_reports.pop(p['id'], None)
            self.save()
            return '服务器配置已删除'
        if action in ('native-inspect', 'native-setup', 'native-apply', 'native-restore'):
            if action in ('native-setup', 'native-apply') and self.statuses.get(p['id'], {}).get('state') != 'connected':
                raise ValueError('请先连接隧道，再接入远端 Magpie')
            script = native_script(action, p, request.get('agent'))
            # Provider refresh / native service startup can take longer than a probe.
            result = self.ssh(p, 'sh -s', script, timeout=55).strip()
            self.native_reports[p['id']] = result[-20000:]
            if action == 'native-apply':
                return '已由服务器 Magpie 的原生适配器完成接入。按本体提示重启对应 agent；之后直接运行原 agent 命令。\n' + result[-4000:]
            return result[-6000:] or '已完成'
        if action in ('install', 'uninstall'):
            if action == 'install':
                if self.statuses.get(p['id'], {}).get('state') != 'connected':
                    raise ValueError('请先连接服务器，再安装 agent 启动命令')
                if p['model'] not in self.local_models(p['localPort']):
                    raise ValueError('所选模型已不可用，请重新选择')
            agent = request.get('agent')
            script = wrapper_script(agent, p, action == 'uninstall')
            result = self.ssh(p, 'sh -s', script).strip()
            if action == 'install':
                result += '\n在服务器项目目录执行 ~/.local/bin/%s-magpie 即可。' % agent
            return result
        raise ValueError('未知操作')

    def publish(self, request_id, result, error=''):
        entry = self.entry()
        if not entry or entry.get('off'):
            return
        options = entry.get('options') or {}
        if options.get('request', {}).get('id') != request_id:
            return
        options['response'] = {'id': request_id, 'result': result, 'error': error, 'snapshot': self.snapshot()}
        # Use Magpie's public writer; never rewrite plugins.json ourselves.
        process = subprocess.run([self.magpie, 'plugin', 'options', self.plugin,
                json.dumps(options, ensure_ascii=False)], capture_output=True, text=True, timeout=15)
        if process.returncode:
            raise RuntimeError(process.stderr[-1000:])

    def loop(self):
        lock = open(self.runtime / 'worker.lock', 'a+')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        lock.seek(0)
        lock.truncate()
        lock.write(str(os.getpid()))
        lock.flush()
        last_id, gateway_down = None, None
        quitting = [False]
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: quitting.__setitem__(0, True))
        try:
            while not quitting[0]:
                try:
                    entry = self.entry()
                    if not entry or entry.get('off'):
                        break
                    request = (entry.get('options') or {}).get('request') or {}
                    response = (entry.get('options') or {}).get('response') or {}
                    request_id = request.get('id')
                    # A restarted worker must not repeat an already acknowledged operation.
                    if request_id and request_id != last_id and request_id != response.get('id'):
                        last_id = request_id
                        try:
                            result, error = self.handle(request), ''
                        except Exception as exc:
                            result, error = '', str(exc)
                        self.publish(request_id, result, error)
                    self.monitor()
                    # Shut down owned SSH children when Magpie's gateway exits.
                    try:
                        self.local_models()
                        gateway_down = None
                    except Exception:
                        gateway_down = gateway_down or time.time()
                        if time.time() - gateway_down > 30:
                            break
                except Exception as exc:
                    print(time.strftime('%Y-%m-%d %H:%M:%S'), str(exc), flush=True)
                time.sleep(1)
        finally:
            for p in self.state['profiles']:
                self.stop(p)
            lock.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    parser.add_argument('--plugin', required=True)
    parser.add_argument('--magpie', required=True)
    arguments = parser.parse_args()
    Manager(arguments.config, arguments.plugin, arguments.magpie).loop()
