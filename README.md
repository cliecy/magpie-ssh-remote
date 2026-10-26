# Magpie SSH Remote

A local [Magpie](https://usemagpie.ai/) plugin that makes your desktop's models available to coding agents on an SSH server.

[中文说明](#中文说明) · [License](LICENSE)

![SSH Remote plugin interface](docs/screenshot.png)

## What it does

- Adds an **SSH Remote** page to Magpie.
- Saves multiple SSH servers and uses your existing SSH keys, aliases and `ProxyJump` settings.
- Opens a reverse tunnel from server loopback to your local Magpie gateway, verifies it from the server, and retries lost connections every 15 seconds.
- Installs optional `codex-magpie` and `claude-magpie` launchers on the server, with a model you select.
- Removes only launchers bearing this plugin's marker; unrelated files and agent configuration are preserved.

```text
Remote Codex / Claude Code
    → server 127.0.0.1:13425
    → encrypted SSH tunnel
    → desktop 127.0.0.1:3425
    → Magpie → your model provider
```

The server does not need its own Magpie installation. Your desktop must stay online with Magpie running.

## Requirements

- macOS or Linux desktop with Magpie, Python 3.9+, and OpenSSH. Windows is currently unsupported.
- Magpie's plugin host runtime (Bun, managed by Magpie).
- A server reachable with noninteractive SSH key authentication, allowing reverse TCP forwarding.
- `curl` and a POSIX shell on the server; Codex or Claude Code installed there if you want its launcher.

The local plugin was verified with Magpie **0.1.1600** on macOS. Linux compatibility is covered by the test workflow, but a live Linux desktop installation has not been verified.

## Install

```sh
magpie plugin add github:cliecy/magpie-ssh-remote
```

Alternatively, clone and install the folder:

```sh
git clone https://github.com/cliecy/magpie-ssh-remote.git
magpie plugin add "$(pwd)/magpie-ssh-remote"
```

On macOS, if `magpie` is not on your PATH, use `/Applications/magpie.app/Contents/MacOS/magpie` instead.

Open **Plugins → SSH Remote** in Magpie's sidebar. Keep the folder in place if you installed a local checkout. Install either the GitHub package or the local checkout, rather than both.

## Use

1. Connect to your server once in a normal terminal, for example `ssh my-server`, to verify its host fingerprint and key authentication.
2. In **SSH Remote**, add the SSH alias or `user@host`, choose a model, and save.
3. Click **Connect**, then **Refresh status** to check the result.
4. Optionally click **Install / Update** beside Codex or Claude Code.
5. On the server, inside your project, run:

   ```sh
   ~/.local/bin/codex-magpie
   # or
   ~/.local/bin/claude-magpie
   ```

Additional arguments pass through to the agent:

```sh
~/.local/bin/codex-magpie -C /path/to/project
```

Choose a complete model ID from the model list, such as `group/default`. After changing the model or port, save, reconnect, and reinstall the launcher. Each server has one launcher per agent; installing from a different profile updates that launcher's settings.

## Lifecycle and limitations

- **Disconnect** stops retrying. Saving edits disconnects the existing tunnel.
- Turning off or removing the plugin stops the helper and its tunnels. Existing enabled profiles reconnect when the plugin starts again.
- The helper exits after the default Magpie gateway has been unavailable for over 30 seconds.
- The UI shows the latest requested snapshot. Click **Refresh status** for current connection information.
- This version installs separate launch commands. It does not change how a plain `codex` or `claude` invocation is configured.
- The desktop loopback gateway currently accepts the client marker keys used by the launchers. A deployment that requires real gateway keys needs additional configuration support.
- Live forwarding and inference against a user's server still need to be verified on that server. The tests exercise process control and generated shell scripts without contacting a real server.

## Security and local data

SSH runs with `BatchMode=yes` and `StrictHostKeyChecking=yes`. The helper does not accept unknown host keys or ask for passwords. Remote commands and model values are shell-quoted.

The requested server listener is `127.0.0.1`; keep the server's `GatewayPorts` set to `no` or `clientspecified` so SSH honors that binding. Other users on a shared server may still reach its loopback listener. Forward this gateway only to servers and accounts you trust: callers there can consume the models and quota available through your desktop gateway.

The plugin does not copy upstream provider credentials or subscription logins to the server. It does use your normal OpenSSH authentication. Profile state and diagnostic logs stay in:

```text
${XDG_CONFIG_HOME:-~/.config}/magpie/ssh-remote/
```

The plugin page uses Magpie's sandbox and its own options as a request/response mailbox. A bundled Python helper handles SSH and returns results through `magpie plugin options`. Installing it permits that helper to run with your local user rights.

Override executable discovery if needed:

```sh
export MAGPIE_SSH_PYTHON=/path/to/python3
export MAGPIE_SSH_BIN=/path/to/magpie
```

These variables must be visible to the process that starts Magpie. Server configurations and logs are runtime data, not part of this repository.

## Remove

Remove remote launchers with the page's **Remove** buttons first if you no longer need them. Then remove the plugin:

```sh
magpie plugin rm magpie-ssh-remote
```

For a local checkout, use its absolute install path instead of the package name. Saved server profiles remain in the runtime folder so reinstalling can restore them; removing the plugin does not erase those files.

## Development

No package build or npm dependencies are required. The frontend is plain HTML/CSS/JavaScript, the entrypoint runs in Magpie's Bun plugin host, and the helper uses Python's standard library.

```sh
python3 -m unittest discover -s tests -p 'test_*.py' -v
node --test tests/*.test.mjs
node --check index.mjs
node --check page/app.js
```

Tests cover shell quoting, remote launcher install/update/remove, protection of existing files and symlinks, reconnect handling, stale UI requests, and local/GitHub installation spec resolution. CI runs them on macOS and Linux.

Manual frontend validation used Magpie's official page bridge inside a browser sandbox, including save, snapshot, install preconditions and delete. A Magpie test instance can still discover real agents even with a separate configuration directory; do not treat `XDG_CONFIG_HOME` alone as complete agent isolation.

See the [Magpie plugin documentation](https://usemagpie.ai/docs/plugins) for the page and host APIs.

## 中文说明

这是一个可从本地文件夹或 GitHub 安装的 Magpie 插件，用 SSH 反向隧道让服务器上的 agent 使用你电脑上的 Magpie。

```sh
magpie plugin add github:cliecy/magpie-ssh-remote
```

安装后，在 Magpie 左侧打开「**SSH 远端接入**」：

1. 新建服务器，填写 SSH 别名或 `user@host`，选择模型并保存。
2. 点击「连接」，再刷新状态确认。
3. 为 Codex 或 Claude Code 点击「安装 / 更新」。
4. 在服务器项目目录运行 `~/.local/bin/codex-magpie` 或 `~/.local/bin/claude-magpie`。

沿用已有的 SSH 密钥与跳板机配置，断线后自动重连。服务器无需安装 Magpie，但需要 `curl` 及对应 agent。首次请先在终端 SSH 登录并确认指纹；插件不会自动接受未知主机。

原 agent 配置保留，通过独立启动命令接入。修改模型后需保存、重新连接并更新启动命令。Mac 需要保持联网，Magpie 持续运行。共享服务器的其他用户可能访问回环端口，请仅连接你信任的服务器。

目前支持 macOS / Linux，Windows 暂不支持。已验证本地插件加载、助手通信和页面流程；真实服务器转发与推理需在目标服务器上验证。

## License

[MIT](LICENSE). This is an independent community plugin, not an official Magpie component.
