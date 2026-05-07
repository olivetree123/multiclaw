# MultiClaw

## 运行
```shell
# 1. 请先编辑环境变量 ./docker/.env

# 2. 启动数据库
cd docker
docker compose -f docker-compose.yaml up -d

# 3. 启动agent服务
cd ..
uv run python main.py --workspace /home/ubuntu/myproject
```

## 一些说明
1. `The command is not run through a system shell`

意思是：程序不会把整段命令交给 `cmd.exe`、PowerShell、bash 这类 shell 去解释执行。

比如不会这样执行：

```python
subprocess.run(command, shell=True)
```

而是先把命令拆成参数列表，再直接执行程序：

```python
subprocess.run(["curl", "wttr.in/Beijing?format=3"], shell=False)
```

这样更安全，因为这些 shell 特性不会生效：

```bash
curl example.com && delete something
curl example.com | other-command
curl example.com > output.txt
```

也就是说，`execute_shell_command` 只会执行白名单里的那个程序本身，比如 `curl`，不会让模型借助 `&&`、管道、重定向等 shell 语法执行额外命令。