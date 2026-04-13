"""
Synapze Enterprise — Load test script
Tests 1,000 concurrent users against the API.

Usage:
    pip install locust
    locust -f scripts/load_test.py --host https://your-domain.com
    # Open http://localhost:8089 and run with 1000 users, ramp 50/s
"""
import json
import random
from locust import HttpUser, task, between, events

TEST_TOKEN = "YOUR_TEST_JWT_TOKEN_HERE"

MESSAGES = [
    "Check my unread emails",
    "What's on my calendar today?",
    "Search my memory for project notes",
    "How many emails do I have?",
    "List my upcoming events this week",
]


class SynapzeUser(HttpUser):
    """Simulates a real user interacting with Synapze."""
    wait_time = between(2, 8)  # realistic think time between requests
    session_id = None

    def on_start(self):
        """Create a session on startup."""
        self.session_id = None
        self.headers = {
            "Authorization": f"Bearer {TEST_TOKEN}",
            "Content-Type": "application/json",
        }

    @task(1)
    def health_check(self):
        """Simulates load balancer probes."""
        with self.client.get("/health", catch_response=True) as r:
            if r.status_code != 200:
                r.failure(f"Health check failed: {r.status_code}")

    @task(3)
    def chat_sync(self):
        """Synchronous agent call (non-streaming)."""
        payload = {
            "message": random.choice(MESSAGES),
            "session_id": self.session_id,
        }
        with self.client.post(
            "/agent/chat",
            json=payload,
            headers=self.headers,
            timeout=60,
            catch_response=True,
            name="/agent/chat",
        ) as r:
            if r.status_code == 200:
                data = r.json()
                self.session_id = data.get("session_id")
            elif r.status_code == 429:
                r.failure("Rate limited")
            else:
                r.failure(f"Chat failed: {r.status_code}")

    @task(5)
    def chat_stream(self):
        """Streaming agent call (SSE)."""
        payload = {
            "message": random.choice(MESSAGES),
            "session_id": self.session_id,
        }
        with self.client.post(
            "/agent/stream",
            json=payload,
            headers=self.headers,
            timeout=120,
            catch_response=True,
            stream=True,
            name="/agent/stream",
        ) as r:
            if r.status_code == 200:
                sid = r.headers.get("X-Session-ID")
                if sid:
                    self.session_id = sid
                # Read until done event
                for line in r.iter_lines():
                    if line:
                        try:
                            event = json.loads(line)
                            if event.get("type") == "done":
                                break
                        except json.JSONDecodeError:
                            pass
            elif r.status_code == 429:
                r.failure("Rate limited")
            elif r.status_code == 503:
                r.failure("Capacity exceeded")
            else:
                r.failure(f"Stream failed: {r.status_code}")

    @task(1)
    def get_sessions(self):
        with self.client.get(
            "/agent/sessions",
            headers=self.headers,
            catch_response=True,
            name="/agent/sessions",
        ) as r:
            if r.status_code != 200:
                r.failure(f"Sessions failed: {r.status_code}")

    @task(1)
    def get_me(self):
        with self.client.get(
            "/auth/me",
            headers=self.headers,
            catch_response=True,
        ) as r:
            if r.status_code not in (200, 401):
                r.failure(f"Me failed: {r.status_code}")


@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    print("=" * 60)
    print("Synapze Load Test Starting")
    print(f"Target: {environment.host}")
    print("=" * 60)

@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    stats = environment.stats.total
    print("\n" + "=" * 60)
    print("LOAD TEST RESULTS")
    print(f"Requests: {stats.num_requests}")
    print(f"Failures: {stats.num_failures} ({stats.fail_ratio:.1%})")
    print(f"Median response time: {stats.median_response_time}ms")
    print(f"p95 response time: {stats.get_response_time_percentile(0.95)}ms")
    print(f"p99 response time: {stats.get_response_time_percentile(0.99)}ms")
    print(f"Requests/sec: {stats.current_rps:.1f}")
    print("=" * 60)
