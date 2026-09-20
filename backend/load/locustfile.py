"""Locust load profile for staging (spec §97: "Load tests").

Run against a real deployment (never CI — CI runs the in-process suite in
``tests/test_load_hot_endpoints.py``):

    locust -f backend/load/locustfile.py --host=https://staging.example.com \
           --users 50 --spawn-rate 5 --run-time 5m

Authenticate by setting ``LOCUST_TOKEN`` (a bearer JWT) or ``LOCUST_EMAIL``
/ ``LOCUST_PASSWORD`` to log in via /api/v1/auth/login at test start.
"""
import os
import time

from locust import HttpUser, between, task


class ContractUser(HttpUser):
    wait_time = between(1, 3)

    def on_start(self):
        token = os.environ.get("LOCUST_TOKEN")
        if token:
            self.client.headers["Authorization"] = f"Bearer {token}"
            return
        email = os.environ.get("LOCUST_EMAIL")
        password = os.environ.get("LOCUST_PASSWORD")
        if email and password:
            resp = self.client.post(
                "/api/v1/auth/login",
                json={"email": email, "password": password},
            )
            if resp.status_code == 200:
                token = resp.json().get("access_token")
                if token:
                    self.client.headers["Authorization"] = f"Bearer {token}"

    @task(4)
    def list_agreements(self):
        self.client.get("/api/v1/agreements?limit=20", name="/agreements")

    @task(3)
    def search(self):
        self.client.get(
            "/api/v1/search/agreements?q=agreement", name="/search"
        )

    @task(2)
    def dashboard_tasks(self):
        self.client.get("/api/v1/dashboard/tasks", name="/dashboard/tasks")

    @task(1)
    def health(self):
        self.client.get("/api/v1/health", name="/health")
