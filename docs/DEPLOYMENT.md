# Data-Agent 部署说明

## 1. 系统配置

当前测试环境配置：

| 项目 | 规格 |
|------|------|
| CPU | 16 核 |
| 内存 | 15 Gi (可用约 1.5 Gi) |
| GPU | 无 |
本地机器本

---

## 2. 依赖说明

### 2.1 核心依赖

| 依赖 | 版本 | 用途 |
|------|------|------|
| fastapi | >=0.115.0 | Web 框架 |
| uvicorn | >=0.30.0 | ASGI 服务器 |
| pydantic | >=2.0.0 | 数据验证 |
| httpx | >=0.27.0 | HTTP 客户端 |
| loguru | >=0.7.2 | 日志库 |
| python-multipart | >=0.0.9 | 表单解析 |
| websockets | >=12.0 | WebSocket 支持 |
| psutil | >=5.9.0 | 系统信息 |
| pypdfium2 | - | 本地PDF 页面分析 |


### 2.2 MinerU 集成
```
https://github.com/opendatalab/MinerU 本地部署，url提供解析接口

# config.py
@dataclass
class MinerUConfig:
    """MinerU configuration."""

    api_url: Optional[str] = ""
    timeout: float = 3600.0
    default_backend: str = "hybrid-auto-engine"
    default_language: str = "ch"
```

### 2.3 大模型后端
```
# 测试使用的是deepseek-flash-v4, 其他也可，需要支持openai api方式
# config.py
@dataclass
class LLMConfig:
    """LLM configuration."""

    provider: str = "openai"
    model: str = "deepseek-v4-flash"
    api_base: Optional[str] = ""
    api_key: Optional[str] = ""
    max_tokens: int = 40960
    temperature: float = 0.01


```


---

## 3. 安装方式

### 3.1 源码安装

```bash
git clone data-agent代码仓库
python -m data-agent.main
```


## 6. 缓存配置

```bash
# 系统缓存目录 (默认)
export MINERU_CACHE_DIR="/tmp/mineru_parse_cache"

# 本地输出目录 (可选)
export MINERU_PARSE_OUTPUT_DIR="/path/to/output"
```

---

## 7. 监控与日志

```bash
# 查看实时日志
tail -f logs/data-agent.log

# 查看 Timeline 日志
grep "TimelineLogger" logs/execution_trace.jsonl
```