"""命令行入口 —— 把项目装成 uv tool 之后，用这两个命令启动。

    uv tool install --editable .     # 见 README「装成 uv tool」一节
    llm-proxy                        # 代理，默认 127.0.0.1:8000
    llm-proxy-fake-upstream          # 假供应商，默认 127.0.0.1:9001

配置还是从环境变量 / .env 读，这里只负责把 uvicorn 拉起来。
"""

import argparse

import uvicorn


def build_parser(prog: str, description: str, default_port: int) -> argparse.ArgumentParser:
    """两个命令共用的参数：监听地址、端口、是否自动重启。"""
    parser = argparse.ArgumentParser(prog=prog, description=description)
    parser.add_argument("--host", default="127.0.0.1", help="监听地址，默认 127.0.0.1")
    parser.add_argument(
        "--port", type=int, default=default_port, help="监听端口，默认 " + str(default_port)
    )
    parser.add_argument("--reload", action="store_true", help="改代码后自动重启，开发时用")
    return parser


def main() -> None:
    """启动代理。"""
    parser = build_parser("llm-proxy", "OpenAI 兼容的聊天补全代理", 8000)
    parser.add_argument("--env-file", default=None, help="指定 .env 的路径；不写就读当前目录的 .env")
    args = parser.parse_args()

    # 用 "模块:应用" 的字符串形式导入，--reload 才能在子进程里重新加载 main 模块。
    uvicorn.run(
        "main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        env_file=args.env_file,
    )


def fake_upstream() -> None:
    """启动本地假供应商，不接真实上游时用它冒烟测试。"""
    parser = build_parser("llm-proxy-fake-upstream", "本地假供应商，测试用", 9001)
    args = parser.parse_args()

    uvicorn.run(
        "dev_fake_upstream:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
