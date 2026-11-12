"""Orchestrate real Kubernetes trials through the in-cluster load controller."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import argparse
import hashlib
import json
from pathlib import Path
import random
import subprocess
import time

from service import http

ROOT = Path(__file__).resolve().parent
KUBE = ROOT / ".kubeconfig-a6-research"
NAMESPACE = "a6-research"
CONTROL = "http://127.0.0.1:18081"
SEED = 20261112


def kubectl(*args, data=None):
    command = ["kubectl", "--kubeconfig", str(KUBE), "-n", NAMESPACE, *args]
    return subprocess.check_output(command, input=data, text=True, stderr=subprocess.STDOUT)


def deploy(image):
    if kubectl("config", "current-context").strip() != "kind-a6-research":
        raise RuntimeError("Refusing a different cluster context")
    objects = [{"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": NAMESPACE}}]
    for role, name in [("provider", "provider"), ("worker", "workers"), ("controller", "controller")]:
        labels = {"app": "capacity-study", "role": role}
        pod = {"metadata": {"labels": labels}, "spec": {
            "automountServiceAccountToken": False,
            "securityContext": {"runAsNonRoot": True, "runAsUser": 65532, "runAsGroup": 65532},
            "terminationGracePeriodSeconds": 5,
            "containers": [{"name": name, "image": image, "imagePullPolicy": "Never",
                "env": [{"name": "ROLE", "value": role}], "ports": [{"containerPort": 8080}],
                "resources": {"requests": {"cpu": "100m", "memory": "96Mi"},
                              "limits": {"cpu": "1500m" if role == "controller" else "1000m", "memory": "1Gi" if role == "controller" else "512Mi"}},
                "securityContext": {"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True,
                                    "capabilities": {"drop": ["ALL"]}},
                "readinessProbe": {"httpGet": {"path": "/health", "port": 8080}, "periodSeconds": 1},
            }]}}
        objects.extend([
            {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": name},
             "spec": {"replicas": 1, "selector": {"matchLabels": labels}, "template": pod}},
            {"apiVersion": "v1", "kind": "Service", "metadata": {"name": name},
             "spec": {"selector": labels, "ports": [{"port": 8080, "targetPort": 8080}]}},
        ])
    kubectl("apply", "-f", "-", data=json.dumps({"apiVersion": "v1", "kind": "List", "items": objects}))
    for name in ["provider", "workers", "controller"]:
        kubectl("rollout", "status", "deployment/" + name, "--timeout=90s")


def addresses():
    pods = json.loads(kubectl("get", "pods", "-l", "role=worker", "-o", "json"))["items"]
    return ["http://" + pod["status"]["podIP"] + ":8080" for pod in pods
            if not pod["metadata"].get("deletionTimestamp") and pod["status"].get("podIP")
            and all(c["ready"] for c in pod["status"].get("containerStatuses", []))]


def scale(replicas):
    kubectl("scale", "deployment/workers", f"--replicas={replicas}")
    kubectl("rollout", "status", "deployment/workers", "--timeout=90s")
    found = addresses()
    if len(found) != replicas:
        raise RuntimeError(f"Expected {replicas} ready workers; found {len(found)}")
    return found


def trace(profile, repetition, duration, mode):
    rng = random.Random(SEED + repetition)
    maximum = 4 if mode == "ollama" else 180
    result = []
    offset = 0.0
    while True:
        offset += rng.expovariate(maximum)
        if offset >= duration:
            break
        rate = maximum if profile != "bursty" or offset % 4 < 2 else maximum / 10
        if rng.random() > rate / maximum:
            continue
        long = rng.random() < 0.5
        tokens = 2200 if long else 1000
        service = (0.22 if long else 0.14) + rng.uniform(-0.02, 0.02)
        if rng.random() < 0.02:
            service += 0.6
        note = "This is a controlled benchmark note. Classify only the HTTP status in the final line. "
        result.append({"id": f"{profile}:{repetition}:{len(result)}", "offset": offset, "tokens": tokens,
                       "service_seconds": service, "task_status": 200 if len(result) % 2 else 500,
                       "prompt_text": note * (60 if long else 30) if mode == "ollama" else ""})
    return result


def configuration(profile, policy, replicas, repetition, mode="emulator", measure=10):
    warmup = 5 if mode == "ollama" else 3
    saturated = profile != "unlimited" and mode == "emulator"
    return {"profile": profile, "policy": policy, "replicas": replicas, "repetition": repetition,
            "mode": mode, "warmup_seconds": warmup, "measurement_seconds": measure,
            "deadline_seconds": 10 if mode == "ollama" else 2,
            "max_attempts": 1,
            "provider": {"tokens_per_second": 40000 if saturated else 1_000_000_000,
                         "requests_per_second": 1000, "mode": mode,
                         "ollama_url": "http://host.docker.internal:11435"},
            "trace": trace(profile, repetition, warmup + measure, mode)}


def check_result(result, config, dynamic=False):
    issues = []
    if len(result["requests"]) != len(config["trace"]):
        issues.append("Missing offered requests")
    expected = {item["id"] for item in config["trace"]}
    observed = {item["id"] for item in result["requests"]}
    if expected != observed:
        issues.append("Request IDs do not match offered trace")
    codes = {sample["code_sha256"] for snapshot in result["resource_samples"] for sample in snapshot["workers"]}
    actual = hashlib.sha256((ROOT / "service.py").read_bytes()).hexdigest()
    if codes != {actual}:
        issues.append("Workers do not run the expected executable")
    hit = {r["pod"] for r in result["requests"] if r.get("pod")}
    if not dynamic and len(hit) != config["replicas"]:
        issues.append("Traffic did not reach every configured replica")
    lags = sorted(r["scheduling_lag_seconds"] for r in result["requests"])
    p95 = lags[max(0, int(len(lags) * 0.95) - 1)] if lags else float("inf")
    if p95 > 0.05:
        issues.append("Generator scheduling-lag p95 exceeds 50ms")
    return issues


def run_trial(out, config, auto=None):
    name = f"{config['mode']}-{config['profile']}-{config['policy']}-{config['replicas']}-{config['repetition']}"
    if auto:
        name += "-autoscale-" + auto
    path = out / f"{name}.json"
    if path.exists():
        print("Already recorded " + name, flush=True)
        return
    config["worker_addresses"] = scale(config["replicas"])
    decisions = []
    previous_upstream = None
    start = time.time()
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(http, CONTROL + "/run", config, 180)
        current = config["replicas"]
        while auto and not future.done():
            time.sleep(2)
            _, live = http(CONTROL + "/live")
            queued = sum(w["queued"] for w in live.get("workers", []))
            active = sum(w["active"] for w in live.get("workers", []))
            upstream = live.get("upstream", {})
            utilisation = None
            if previous_upstream and upstream.get("time", 0) > previous_upstream.get("time", 0):
                elapsed = upstream["time"] - previous_upstream["time"]
                used = upstream["declared_tokens_accepted"] - previous_upstream["declared_tokens_accepted"]
                utilisation = used / elapsed / config["provider"]["tokens_per_second"]
            previous_upstream = upstream or previous_upstream
            # Explicit prototype controller, not Kubernetes HPA or KEDA.
            headroom = auto != "budget-aware" or (utilisation is not None and utilisation < 0.95)
            decision = {"time": time.time(), "replicas_before": current, "queued": queued,
                        "active": active, "upstream_budget_utilisation": utilisation, "headroom": headroom}
            if queued > 8 and current < 8 and headroom:
                current = min(current * 2, 8)
                updated = scale(current)
                http(CONTROL + "/workers", {"addresses": updated})
            decisions.append({**decision, "replicas_after": current})
        status, result = future.result()
    if status != 200:
        raise RuntimeError(f"Controller failed: {result}")
    issues = check_result(result, config, dynamic=auto is not None)
    # Internal pod addresses are unnecessary in retained research data.
    config = {k: v for k, v in config.items() if k != "worker_addresses"}
    payload = {"configuration": config, "autoscaling": auto, "decisions": decisions,
               "driver_started_at": start, "valid": not issues, "validation_issues": issues, "result": result}
    path.write_text(json.dumps(payload))
    print(f"Recorded {name}; valid={not issues}", flush=True)
    if any("executable" in issue or "Request IDs" in issue for issue in issues):
        raise RuntimeError("Trial integrity failure; raw data preserved")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["deploy", "smoke", "emulator", "retry", "autoscale", "ollama"])
    parser.add_argument("--image", default="alien6-research-capacity:study-v1")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    if args.out is None:
        args.out = ROOT / "runs" / ("2026-11-12-preflight" if args.stage == "smoke" else "2026-11-12-capacity")
    if args.stage == "deploy":
        deploy(args.image)
        return
    args.out.mkdir(parents=True, exist_ok=True)
    manifest = {"utc": datetime.now(timezone.utc).isoformat(), "seed": SEED,
                "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                "service_sha256": hashlib.sha256((ROOT / "service.py").read_bytes()).hexdigest(),
                "kubernetes": json.loads(kubectl("version", "-o", "json")), "image": args.image}
    manifest["stage"] = args.stage
    manifest["driver_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    manifest["image_id"] = subprocess.check_output(["docker", "image", "inspect", args.image, "--format", "{{.Id}}"], text=True).strip()
    if args.stage == "ollama":
        _, tags = http("http://127.0.0.1:11435/api/tags")
        model = next(m for m in tags["models"] if m["name"] == "qwen3.6:latest")
        if model["digest"] != "07d35212591fc27746f0a317c975a6d68754fb38e9053d82e25f06057af28522":
            raise RuntimeError("Ollama model differs from protocol")
        manifest["ollama_model"] = {k: model[k] for k in ["name", "digest", "details"]}
    manifest_path = args.out / f"manifest-{args.stage}.json"
    if manifest_path.exists():
        previous = json.loads(manifest_path.read_text())
        for key in ["service_sha256", "driver_sha256", "image_id"]:
            if previous[key] != manifest[key]:
                raise RuntimeError("Executable changed: use a new run directory")
    else:
        manifest_path.write_text(json.dumps(manifest, indent=2))
    if args.stage == "smoke":
        config = configuration("unlimited", "none", 2, 0, measure=2)
        config["trace"] = [{**item, "offset": index * 0.1} for index, item in enumerate(config["trace"][:50])]
        run_trial(args.out, config)
        return
    jobs = []
    if args.stage == "emulator":
        jobs = [configuration(profile, policy, replicas, rep) for profile in ["unlimited", "quota", "bursty"]
                for policy in ["none", "global"] for replicas in [1, 2, 4, 8] for rep in range(3)]
    elif args.stage == "retry":
        for rep in range(3):
            for attempts in [1, 2]:
                config = configuration("quota", "none", 2, rep)
                config["max_attempts"] = attempts
                config["profile"] = f"quota-retry-{attempts}"
                jobs.append(config)
    elif args.stage == "autoscale":
        for rep in range(3):
            for strategy in ["queue-only", "budget-aware"]:
                config = configuration("quota", "global", 1, rep, measure=30)
                jobs.append((config, strategy))
    else:
        jobs = [configuration("unlimited", "none", replicas, rep, mode="ollama", measure=20)
                for replicas in [1, 2, 4, 8] for rep in range(3)]
    random.Random(SEED).shuffle(jobs)
    for job in jobs:
        if isinstance(job, tuple):
            run_trial(args.out, job[0], job[1])
        else:
            run_trial(args.out, job)


if __name__ == "__main__":
    main()
