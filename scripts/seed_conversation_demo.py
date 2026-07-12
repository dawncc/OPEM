"""Add a multi-turn code and tool execution demo to the local RL-CC-9 session."""
import httpx

payload = {
    "project": "codex-rl-cc-9-test",
    "session_id": "rl-cc-9-chat-002",
    "source_host": "RL-CC-9",
    "messages": [
        {"sequence": 3, "role": "user", "content": "请实现一个 Python 健康检查函数，并执行测试。"},
        {"sequence": 4, "role": "assistant", "content": "可以先实现一个最小版本：\n\n```python\ndef memory_health(client):\n    response = client.get('/api/v1/health')\n    response.raise_for_status()\n    return response.json()['status'] == 'ok'\n```\n\n随后运行 pytest 验证。"},
        {"sequence": 5, "role": "tool", "content": "$ pytest tests/test_health.py -q\n1 passed in 0.24s", "metadata": {"tool_name": "pytest", "status": "completed", "duration_ms": 240}},
        {"sequence": 6, "role": "user", "content": "增加超时测试，并修复发现的问题。"},
        {"sequence": 7, "role": "assistant", "content": "新增超时参数并先运行测试：\n\n```python\ndef memory_health(client, timeout=3.0):\n    return client.get('/api/v1/health', timeout=timeout).json()\n```"},
        {"sequence": 8, "role": "tool", "content": "$ pytest tests/test_health.py -q\nFAILED tests/test_health.py::test_timeout\nTypeError: fake_get() got an unexpected keyword argument 'timeout'", "metadata": {"tool_name": "pytest", "status": "failed", "duration_ms": 318}},
        {"sequence": 9, "role": "assistant", "content": "失败原因是测试替身没有接收 timeout。修复测试替身后重新运行：\n\n```python\ndef fake_get(path, **kwargs):\n    return FakeResponse({'status': 'ok'})\n```"},
        {"sequence": 10, "role": "tool", "content": "$ pytest tests/test_health.py -q\n2 passed in 0.29s", "metadata": {"tool_name": "pytest", "status": "completed", "duration_ms": 290}},
    ],
}

if __name__ == "__main__":
    response = httpx.post("http://127.0.0.1:8000/api/v1/chat/messages", json=payload, timeout=10)
    response.raise_for_status()
    print(response.json())
