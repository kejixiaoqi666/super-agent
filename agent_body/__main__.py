import argparse
import json
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="AgentWorkbench body with SuperBrain 2.0")
    parser.add_argument("--kernel", type=Path, default=Path(__file__).resolve().parents[2] / "superbrain-2.0" / "python")
    parser.add_argument("--data", default=".body-data")
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--mode", choices=["read-only", "workspace", "unrestricted"], default="workspace")
    parser.add_argument("--session", default="local")
    parser.add_argument("--telegram", action="store_true")
    args = parser.parse_args()
    sys.path.insert(0, str(args.kernel.resolve()))
    from .runtime import Body
    body = Body(args.data, args.workspace, args.mode)
    try:
        if args.telegram:
            from .telegram import serve
            serve(body)
        else:
            print("AgentWorkbench / SuperBrain 2.0. /quit /state /tick")
            while True:
                try:
                    message = input("> ").strip()
                except EOFError:
                    break
                if message == "/quit":
                    break
                if not message:
                    continue
                if message == "/state":
                    print(json.dumps(body.brain(args.session).state(), ensure_ascii=False, default=str))
                elif message == "/tick":
                    print(json.dumps(body.tick(args.session), ensure_ascii=False, default=str))
                else:
                    print(body.chat(args.session, message)["reply"])
    finally:
        body.close()


if __name__ == "__main__":
    main()
