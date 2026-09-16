//! Super-Agent 客户端壳（Rust）
//!
//! ROADMAP 定位：客户端层用 Rust（稳定+高性能），做本地压缩执行 + 跨平台入口。
//! 大脑=独立内核(superbrain-2.0)，身体=Python 载体(super-agent)，客户端=本壳。
//! 客户端不 import 身体内部，而是通过 `sa`（Python 身体的 CLI 入口）启动身体，
//! 保持"换内核/换身体 agent 主体零改动"的契约分离。
//!
//! 职责：
//!   1. 定位并调用 Python 身体入口（sa / python -m agent_body）
//!   2. 透传用户输入（交互式 REPL）
//!   3. 作为一键安装后 `sa` 命令的 Rust 启动器（后续可加本地压缩/加密等原生能力）
//!
//! 本骨架保持最小可编译可运行，后续能力（原生图片压缩/加密/守护）逐步加。

use std::env;
use std::process::{Command, exit};

const BODY_MODULE: &str = "agent_body";

fn main() {
    let args: Vec<String> = env::args().skip(1).collect();

    // 1) 定位 Python
    let python = find_python();
    if python.is_none() {
        eprintln!("!! 未找到 python3/python。请先安装 Python 3.10+，再运行 sa。");
        exit(1);
    }
    let python = python.unwrap();

    // 2) 无参数 → 交互式 REPL 启动身体；有参数 → 透传给身体
    let mut cmd = Command::new(&python);
    cmd.arg("-m").arg(BODY_MODULE);
    cmd.args(&args);

    // 3) 继承标准输入输出，保持交互式 REPL
    let mut child = match cmd.spawn() {
        Ok(c) => c,
        Err(e) => {
            eprintln!("!! 启动身体失败: {}", e);
            eprintln!("   请确认已安装 super-agent 身体层（pip install super-agent）");
            exit(1);
        }
    };
    // 阻塞等待身体退出，透传退出码
    let status = child.wait();
    let code = match status {
        Ok(s) => s.code().unwrap_or(1),
        Err(_) => 1,
    };
    exit(code);
}

/// 跨平台定位 Python 解释器。
fn find_python() -> Option<String> {
    // Windows 优先 python，Unix 优先 python3
    let candidates: &[&str] = if cfg!(windows) {
        &["python", "py"]
    } else {
        &["python3", "python"]
    };
    for cand in candidates {
        if which(cand) {
            return Some((*cand).to_string());
        }
    }
    None
}

/// 简易 which：检查命令是否在 PATH 中。
fn which(name: &str) -> bool {
    if cfg!(windows) {
        Command::new("where")
            .arg(name)
            .output()
            .map(|o| o.status.success())
            .unwrap_or(false)
    } else {
        Command::new("sh")
            .arg("-c")
            .arg(format!("command -v {}", name))
            .output()
            .map(|o| o.status.success())
            .unwrap_or(false)
    }
}
