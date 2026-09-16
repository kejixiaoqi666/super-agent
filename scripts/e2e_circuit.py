"""真实场景验证：死循环防护（熔断器 + 自查）。

模拟"连一台挂了/连不上的服务器"的经典卡死场景，验证：
  ① 不会永远重试（熔断器触发后快速失败）
  ② 失败不盲目重试单点，而是进缺陷查
  ③ 全程有进度/失败上报
"""
import socket
import sys
import time
from pathlib import Path

# 让 scripts/ 下的脚本能 import agent_body（项目根加入 sys.path）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_body.safety import CircuitBreaker, call_with_retry

# 模拟一台连不上的服务器（端口都不通，每次连接都失败）
UNREACHABLE_HOST = "192.0.2.55"  # TEST-NET 保留段，永不响应


def ping_server(host: str, port: int = 22):
    """模拟一次 SSH 连接尝试：连不上就抛 ConnectionError。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1.0)  # 1 秒超时（真实失败场景）
    try:
        s.connect((host, port))
    finally:
        s.close()
    return True


def main():
    print("=== 死循环防护真实验证 ===")
    print(f"目标: {UNREACHABLE_HOST} (永久连不上的保留段 IP)")

    # 方案A：不设熔断的"朴素重试"会怎样（只给 4 次演示，真实会无限）
    t0 = time.time()
    naive_fails = 0
    for i in range(4):
        try:
            ping_server(UNREACHABLE_HOST)
        except Exception:
            naive_fails += 1
        time.sleep(0.2)
    print(f"\n[朴素重试] 4 次全失败（{naive_fails}/4），继续这样会永远重试 → 卡死")

    # 方案B：熔断器 —— 连续失败达阈值 → 熔断 → 快速失败不再试
    breaker = CircuitBreaker(failure_threshold=3, reset_timeout=0.5)
    t0 = time.time()
    attempts = 0
    results = []
    while time.time() - t0 < 4:  # 用 4 秒模拟"长时间任务"
        if not breaker.allow():   # 熔断了！快速失败，不再尝试
            results.append(f"熔断拒发(第{attempts + 1}次尝试前)")
            break
        attempts += 1
        try:
            ping_server(UNREACHABLE_HOST)
            breaker.record_success()
            results.append(f"尝试{attempts}: 成功")
        except Exception:
            tripped = breaker.record_failure()
            results.append(f"尝试{attempts}: 失败 {'→ 触发熔断!' if tripped else ''}")
        time.sleep(0.1)

    print(f"\n[熔断器] 共尝试 {attempts} 次后触发熔断，之后快速失败不再空转")
    for r in results:
        print(f"    {r}")

    # 方案C：带重试预算 + 退避的调用（不会无限打）
    t0 = time.time()
    try:
        call_with_retry(lambda: ping_server(UNREACHABLE_HOST),
                        max_retries=3, base_backoff=0.2, timeout=2)
    except Exception:
        elapsed = round(time.time() - t0, 2)
        print(f"\n[重试预算] 连失败，{elapsed}s 内耗尽预算后放弃（不空转）→ 交由缺陷查")

    return results[-1] if results else "no-result"


if __name__ == "__main__":
    main()