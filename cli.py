"""命令行入口 —— 把项目装成 uv tool 之后，用这两个命令启动。

    uv tool install --editable .     # 见 README「装成 uv tool」一节
    llm-proxy                        # 代理，默认 127.0.0.1:8000
    llm-proxy-fake-upstream          # 假供应商，默认 127.0.0.1:9001

llm-proxy 的 host / port 按「命令行 > ~/.llm-proxy.yaml 的 server 段 > 内置默认」取值；
假供应商不读用户配置文件，只认命令行和内置默认。配置本身还是从环境变量 / .env 读，
这里只负责把 uvicorn 拉起来。
"""

import argparse

import uvicorn

# 命令行和 server 段都没给时用的内置默认值。
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PROXY_PORT = 8000
DEFAULT_FAKE_UPSTREAM_PORT = 9001


def build_parser(prog: str, description: str, host_help: str, port_help: str) -> argparse.ArgumentParser:
    """两个命令共用的参数：监听地址、端口、是否自动重启。

    --host / --port 不设默认值（缺省 None 表示「命令行上没给」），
    到底用什么由调用方解析参数之后自己补 —— 优先级规则也就都留在调用方那里。
    """
    parser = argparse.ArgumentParser(prog=prog, description=description)
    parser.add_argument("--host", help=host_help)
    parser.add_argument("--port", type=int, help=port_help)
    parser.add_argument("--reload", action="store_true", help="改代码后自动重启，开发时用")
    return parser


def main() -> None:
    """启动代理：host / port 按「命令行 > 配置文件的 server 段 > 内置默认」取值。"""
    parser = build_parser(
        "llm-proxy",
        "OpenAI 兼容的聊天补全代理",
        "监听地址；不写先看 ~/.llm-proxy.yaml 的 server.host，都没有就用 " + DEFAULT_HOST,
        "监听端口；不写先看 ~/.llm-proxy.yaml 的 server.port，都没有就用 " + str(DEFAULT_PROXY_PORT),
    )
    args = parser.parse_args()

    # 解析完参数再导入 config：--help 用不着配置文件。
    # 导入 config 会顺带加载并校验 ~/.llm-proxy.yaml，配置有问题就在起服务之前直接报错。
    from config import load_server_config

    server = load_server_config()
    host = args.host
    if host is None:
        host = server.host
    if host is None:
        host = DEFAULT_HOST
    port = args.port
    if port is None:
        port = server.port
    if port is None:
        port = DEFAULT_PROXY_PORT

    # 用 "模块:应用" 的字符串形式导入，--reload 才能在子进程里重新加载 main 模块。
    uvicorn.run(
        "main:app",
        host=host,
        port=port,
        reload=args.reload,
        env_file=args.env_file,
    )