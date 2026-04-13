"""Synapze Enterprise — Prometheus metrics"""
import re
from app.config import settings

try:
    from prometheus_client import Counter, Histogram, Gauge, CollectorRegistry, generate_latest, CONTENT_TYPE_LATEST
    PROMETHEUS_AVAILABLE = True
    _registry = CollectorRegistry()
    REQUEST_COUNT = Counter("synapze_http_requests_total", "Total HTTP requests", ["method", "path", "status"], registry=_registry)
    REQUEST_DURATION = Histogram("synapze_http_request_duration_ms", "HTTP request duration ms", ["method", "path"], buckets=[5,10,25,50,100,250,500,1000,2500,5000], registry=_registry)
    AGENT_RUNS = Counter("synapze_agent_runs_total", "Agent runs", ["mode", "status"], registry=_registry)
    TOOL_CALLS = Counter("synapze_tool_calls_total", "Tool calls", ["tool", "status"], registry=_registry)
    TOKENS_TOTAL = Counter("synapze_tokens_total", "Tokens consumed", ["direction"], registry=_registry)
    ACTIVE_STREAMS = Gauge("synapze_active_streams", "Active streaming connections", registry=_registry)
except ImportError:
    PROMETHEUS_AVAILABLE = False

def _norm(path: str) -> str:
    path = re.sub(r"/[0-9a-f-]{36}", "/{uuid}", path)
    path = re.sub(r"/\d+", "/{id}", path)
    return path

def record_request(method, path, status, duration_ms):
    if not PROMETHEUS_AVAILABLE or not settings.PROMETHEUS_ENABLED: return
    p = _norm(path)
    REQUEST_COUNT.labels(method=method, path=p, status=str(status)).inc()
    REQUEST_DURATION.labels(method=method, path=p).observe(duration_ms)

def record_agent_run(mode, status, duration: float = 0): 
    if PROMETHEUS_AVAILABLE and settings.PROMETHEUS_ENABLED:
        AGENT_RUNS.labels(mode=mode, status=status).inc()

def record_tool_call(tool, status):
    if PROMETHEUS_AVAILABLE and settings.PROMETHEUS_ENABLED:
        TOOL_CALLS.labels(tool=tool, status=status).inc()

def record_tokens(tokens_in, tokens_out):
    if not PROMETHEUS_AVAILABLE or not settings.PROMETHEUS_ENABLED: return
    TOKENS_TOTAL.labels(direction="in").inc(tokens_in)
    TOKENS_TOTAL.labels(direction="out").inc(tokens_out)

def get_metrics_output():
    if not PROMETHEUS_AVAILABLE: return b"# prometheus_client not installed\n", "text/plain"
    return generate_latest(_registry), CONTENT_TYPE_LATEST
