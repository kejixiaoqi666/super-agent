from .server import serve
import argparse

parser = argparse.ArgumentParser(description="Loopback Agent Memory Plane")
parser.add_argument("--config", default="config.toml")
serve(parser.parse_args().config)
