#!/usr/bin/env python3
"""多模型调用封装 — 供策略内化层 TDD 流程使用。

orchestrator（主评审模型）通过本脚本独立调用多个模型角色，
避免单模型盲区。角色分工示例：writer 写测试、reviewer 评审、
executor 写实现、fixer 修 issue。

用法:
    python scripts/call_model.py --role writer   "prompt"
    python scripts/call_model.py --role reviewer "prompt"
    python scripts/call_model.py --role executor "prompt"
    python scripts/call_model.py --model your-model-name "prompt"

也可作为模块导入: from call_model import call

注意：端点和 API key 从环境变量读取。复制 .env.example 为 .env 填入真实值。
"""
import argparse, json, os, sys, time

# ---- 端点配置（从环境变量 / .env 读 key） ----
_ENV = {}
_env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
if os.path.exists(_env_path):
    with open(_env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                _ENV[k.strip()] = v.strip().strip('"').strip("'")

# 供应商配置 — 占位符，真实端点请在 .env 中覆盖（见 .env.example）
# provider_a/b/c 可映射到你实际使用的任意 OpenAI 兼容供应商
PROVIDERS = {
    "provider_a": {
        "base_url": _ENV.get("PROVIDER_A_BASE_URL", "YOUR_LLM_ENDPOINT_A"),
        "api_key": _ENV.get("YOUR_API_KEY_A", ""),
        "endpoint": "/chat/completions",
    },
    "provider_b": {
        "base_url": _ENV.get("PROVIDER_B_BASE_URL", "YOUR_LLM_ENDPOINT_B"),
        "api_key": _ENV.get("YOUR_API_KEY_B", ""),
        "endpoint": "/chat/completions",
    },
    "provider_c": {
        "base_url": _ENV.get("PROVIDER_C_BASE_URL", "YOUR_LLM_ENDPOINT_C"),
        "api_key": _ENV.get("YOUR_API_KEY_C", ""),
        "endpoint": "/v1/chat/completions",
    },
}

# 角色 → (provider, model)
ROLES = {
    "writer":   ("provider_a", "your-model-for-writing"),     # 写测试用例（重）
    "reviewer": ("provider_b", "your-model-for-review"),      # 评审
    "executor": ("provider_a", "your-model-for-execution"),   # 写实现/执行（快）
    "fixer":    ("provider_c", "your-model-for-fixing"),      # 修 issue（高精度）
}

def _resolve_provider(model_name: str) -> str:
    """按 model 名推断 provider。按你的实际模型命名调整。"""
    name = model_name.lower()
    if "deepseek" in name or "flash" in name or "pro" in name:
        return "provider_a"
    elif "glm" in name:
        return "provider_b"
    elif "gpt" in name:
        return "provider_c"
    return "provider_a"  # 默认


def call(prompt: str, role: str = None, model: str = None,
         system: str = None, temperature: float = 0.2,
         max_tokens: int = 16384, timeout: int = 300) -> dict:
    """调用模型。返回 {ok, content, model, elapsed, usage, raw}。

    role 与 model 二选一；role 优先。temperature 默认 0.2（写代码/评审要稳）。
    """
    import requests

    if role:
        provider_name, model = ROLES[role]
    else:
        # 按 model 名推断 provider
        provider_name = _resolve_provider(model or "")

    prov = PROVIDERS[provider_name]
    if not prov["api_key"]:
        return {"ok": False, "error": f"no api key for {provider_name} (check .env)"}

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    url = prov["base_url"].rstrip("/") + prov["endpoint"]
    headers = {"Authorization": f"Bearer {prov['api_key']}",
               "Content-Type": "application/json"}
    payload = {"model": model, "messages": messages,
               "temperature": temperature, "max_tokens": max_tokens,
               "stream": False}

    t0 = time.time()
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=timeout)
        elapsed = round(time.time() - t0, 1)
        if r.status_code != 200:
            return {"ok": False, "status": r.status_code,
                    "error": r.text[:500], "model": model, "elapsed": elapsed}
        data = r.json()
        content = data["choices"][0]["message"]["content"]
        return {
            "ok": True, "content": content, "model": model,
            "elapsed": elapsed,
            "usage": data.get("usage", {}),
        }
    except Exception as e:
        return {"ok": False, "error": str(e)[:300], "model": model,
                "elapsed": round(time.time() - t0, 1)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("prompt", help="prompt 文本，或 - 读 stdin")
    ap.add_argument("--role", choices=list(ROLES), help="角色")
    ap.add_argument("--model", help="直接指定 model 名")
    ap.add_argument("--system", help="system prompt")
    ap.add_argument("--max-tokens", type=int, default=4096)
    ap.add_argument("--temp", type=float, default=0.2)
    args = ap.parse_args()

    prompt = sys.stdin.read() if args.prompt == "-" else args.prompt
    res = call(prompt, role=args.role, model=args.model,
               system=args.system, temperature=args.temp,
               max_tokens=args.max_tokens)
    if res.get("ok"):
        print(res["content"])
        sys.stderr.write(f"\n[model={res['model']} elapsed={res['elapsed']}s "
                         f"usage={res.get('usage',{})}]\n")
    else:
        sys.stderr.write(f"ERROR: {res}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
