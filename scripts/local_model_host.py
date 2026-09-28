#!/usr/bin/env python3
"""Launch a local OpenAI-compatible inference server for the policy bot.

This script starts a vLLM or SGLang backend in a dedicated process, then the
adapter in scripts/local_model_service.py talks to it. The backend is intentionally
isolated from the Rasa environment because vLLM/SGLang have heavier GPU/runtime
requirements than the main app stack.
"""
from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def build_backend_command(model_name: str, host: str, port: int) -> list[str]:
    backend = os.getenv('MODEL_BACKEND', 'vllm').strip().lower()
    model_name = model_name or os.getenv('MODEL_NAME', 'HuggingFaceTB/SmolLM2-360M-Instruct')
    cmd: list[str]
    if backend == 'sglang':
        device = os.getenv('MODEL_DEVICE', 'cpu').strip().lower() or 'cpu'
        cmd = [
            str(ROOT / '.venv/bin/python'),
            '-m',
            'sglang.launch_server',
            '--model-path', model_name,
            '--host', host,
            '--port', str(port),
            '--dtype', 'float16',
            '--trust-remote-code',
            '--device', device,
        ]
    else:
        cmd = [
            str(ROOT / '.venv/bin/python'),
            '-m',
            'vllm.entrypoints.openai.api_server',
            '--model', model_name,
            '--host', host,
            '--port', str(port),
            '--served-model-name', model_name,
            '--max-model-len', '2048',
        ]
    return cmd


def main() -> int:
    model_name = os.getenv('MODEL_NAME', os.getenv('POLICY_GENERATION_MODEL', 'HuggingFaceTB/SmolLM2-360M-Instruct'))
    host = os.getenv('MODEL_HOST', '127.0.0.1')
    port = int(os.getenv('MODEL_BACKEND_PORT', os.getenv('MODEL_PORT', '9001')))
    cmd = build_backend_command(model_name, host, port)
    print(f'Starting model backend: {shlex.join(cmd)}', flush=True)
    device = os.getenv('MODEL_DEVICE', 'cpu').strip().lower() or 'cpu'
    env = {**os.environ, 'HF_HUB_DISABLE_TELEMETRY': '1', 'VLLM_TARGET_DEVICE': device}
    if device == 'cpu':
        env.setdefault('VLLM_CPU_KVCACHE_SPACE', '4')
    process = subprocess.Popen(cmd, cwd=str(ROOT), env=env)
    try:
        return process.wait()
    except KeyboardInterrupt:
        process.terminate()
        return 130


if __name__ == '__main__':
    raise SystemExit(main())
