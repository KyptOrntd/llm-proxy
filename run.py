import uvicorn

from config import CONFIG


def main() -> None:
    uvicorn.run("main:app", host=CONFIG.server.host, port=CONFIG.server.port)


if __name__ == '__main__':
    main()
