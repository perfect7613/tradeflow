"""TradeFlow: a money control room for MSME exporters."""


def main() -> None:
    from .api import main as run

    run()
