import atexit
import json
import subprocess
import threading
import time

from pathlib        import Path
from urllib.request import urlopen, Request
from urllib.error   import URLError, HTTPError

from core.config    import config, Config


class ModelManagerError(Exception):
    """Raised when model management fails"""
    ...


class ModelManager:
    def __init__(
        self,
        cfg: Config = config,
        host: str = "127.0.0.1",
        port: int = 8080,
        startup_timeout: int = 180
    ):
        self.config   = cfg
        
        self.host     = host
        self.port     = port
        self.base_url = f"http://{host}:{port}"

        self.startup_timeout = startup_timeout

        self.server_path = self.config.llama_dir / "build/bin/llama-server"

        self.process: subprocess.Popen | None = None
        self.log_file = None

        self.active_model: str | None = None

        self.lock = threading.RLock()
        
        atexit.register(self.stop)


    @property
    def is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def _build_command(self, model_name: str) -> list[str]:
        model     = self.config.get_model(model_name)
        inference = self.config.inference

        return [
            str(self.server_path),

            "-m",   str(model.model_path),
            "-ngl", str(inference.gpu_layers),
            "-c",   str(model.context_size),
            "-t",   str(inference.threads),
            "-fa",  inference.flash_attn,
            "-ctk", inference.cache_type_k,
            "-ctv", inference.cache_type_v,

            "-np", "1",

            "--host", self.host,
            "--port", str(self.port),

            "--temp",  str(inference.temperature),
            "--top-p", str(inference.top_p),
            "--top-k", str(inference.top_k),
            "--min-p", str(inference.min_p),
            "--jinja",
            "--reasoning", "off",
            "--metrics",
        ]

    def _health_check(self) -> bool:
        try:
            with urlopen(f"{self.base_url}/health",timeout=2) as response:
                return response.status == 200
        except (URLError, HTTPError, TimeoutError, OSError):
            return False

    def _wait_until_ready(self):
        deadline = time.monotonic() + self.startup_timeout

        while time.monotonic() < deadline:
            if not self.is_running:
                raise ModelManagerError("llama-server exited unexpectedly during startup.")
            if self._health_check():
                return
            time.sleep(1)

        raise ModelManagerError(f"Model startup timed out after {self.startup_timeout}s.")

    def start(self, model_name: str):
        with self.lock:
            model = self.config.get_model(model_name)
            
            if self.is_running:
                if self.active_model == model_name:
                    return
                
                self.stop()
                
            elif self.process is not None:
                self.stop()

            if not self.server_path.is_file():
                raise ModelManagerError(f"llama-server not found: {self.server_path}")

            if not model.model_path.is_file():
                raise ModelManagerError(f"Model not found: {model.model_path}")

            if not model.personality_path.is_file():
                raise ModelManagerError(f"Personality not found: {model.personality_path}")

            self.config.log_dir.mkdir(
                parents  = True,
                exist_ok = True
            )

            log_path = self.config.log_dir / f"{model_name}.log"

            command = self._build_command(model_name)

            self.log_file = log_path.open("a", encoding="utf-8")

            try:
                self.process = subprocess.Popen(
                    command,
                    cwd=self.config.llama_dir,
                    stdout=self.log_file,
                    stderr=subprocess.STDOUT,
                    shell=False,
                    start_new_session=True
                )

                self.active_model = model_name

                self._wait_until_ready()

            except Exception as exc:
                self.stop()

                if isinstance(exc, ModelManagerError):
                    raise

                raise ModelManagerError(f"Failed to start {model_name}: {exc}") from exc

    def stop(self):
        with self.lock:
            process = self.process

            if process is not None:
                if process.poll() is None:
                    process.terminate()

                    try:
                        process.wait(timeout=15)

                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()

                self.process = None

            self.active_model = None

            if self.log_file is not None:
                self.log_file.close()
                self.log_file = None

    def switch(self, model_name: str):
        with self.lock:
            if self.active_model == model_name and self.is_running:
                return

            self.stop()
            self.start(model_name)

    def status(self) -> dict:
        is_up = self.is_running or self._health_check()
        return {
            "running": is_up,
            "active_model": self.active_model or (self.config.inference.default_model if is_up else None),
            "server_url": self.base_url if is_up else None
        }

    def get_metrics(self) -> dict:
        """Fetch and parse live server telemetry from llama-server (/metrics, /slots, /props)"""
        if not (self.is_running or self._health_check()):
            return {
                "active": False,
                "server_url": None,
                "prompt_tokens_total": 0,
                "prompt_tokens_cached_total": 0,
                "tokens_predicted_total": 0,
                "prompt_eval_speed": None,
                "generation_speed": None,
                "kv_cache_tokens": None,
                "kv_cache_usage_ratio": None,
                "n_ctx": None,
                "raw": {}
            }

        metrics = {}
        # 1. Prometheus /metrics endpoint (enabled by --metrics flag)
        try:
            req = Request(f"{self.base_url}/metrics", headers={"User-Agent": "PAIN"})
            with urlopen(req, timeout=1.0) as resp:
                text = resp.read().decode("utf-8", errors="replace")
                for line in text.splitlines():
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    parts = line.split()
                    if len(parts) >= 2:
                        key = parts[0]
                        val = parts[1]
                        try:
                            metrics[key] = float(val) if ("." in val or "e" in val.lower()) else int(val)
                        except ValueError:
                            metrics[key] = val
        except Exception:
            pass

        # 2. Server slots endpoint
        slots = []
        try:
            req = Request(f"{self.base_url}/slots", headers={"User-Agent": "PAIN"})
            with urlopen(req, timeout=1.0) as resp:
                slots_data = json.loads(resp.read().decode("utf-8"))
                if isinstance(slots_data, list):
                    slots = slots_data
        except Exception:
            pass

        # 3. Server props endpoint
        props = {}
        try:
            req = Request(f"{self.base_url}/props", headers={"User-Agent": "PAIN"})
            with urlopen(req, timeout=1.0) as resp:
                props = json.loads(resp.read().decode("utf-8"))
        except Exception:
            pass

        # Extract llama.cpp Prometheus metrics
        prompt_eval_speed = metrics.get("llamacpp:prompt_tokens_seconds")
        gen_speed = metrics.get("llamacpp:predicted_tokens_seconds")
        prompt_tokens_total = metrics.get("llamacpp:prompt_tokens_total", 0)
        prompt_cached_total = metrics.get("llamacpp:prompt_tokens_cached_total", 0)
        tokens_predicted_total = metrics.get("llamacpp:tokens_predicted_total", 0)
        kv_cache_tokens = metrics.get("llamacpp:kv_cache_tokens")
        kv_cache_usage_ratio = metrics.get("llamacpp:kv_cache_usage_ratio")

        slot_past = None
        slot_ctx = None
        if slots and isinstance(slots, list) and len(slots) > 0:
            first_slot = slots[0]
            if isinstance(first_slot, dict):
                slot_past = first_slot.get("n_past")
                slot_ctx = first_slot.get("n_ctx")

        return {
            "active": True,
            "server_url": self.base_url,
            "prompt_tokens_total": prompt_tokens_total,
            "prompt_tokens_cached_total": prompt_cached_total,
            "tokens_predicted_total": tokens_predicted_total,
            "prompt_eval_speed": round(prompt_eval_speed, 1) if isinstance(prompt_eval_speed, (int, float)) else None,
            "generation_speed": round(gen_speed, 1) if isinstance(gen_speed, (int, float)) else None,
            "kv_cache_tokens": kv_cache_tokens if kv_cache_tokens is not None else slot_past,
            "kv_cache_usage_ratio": round(kv_cache_usage_ratio, 3) if isinstance(kv_cache_usage_ratio, (int, float)) else None,
            "n_ctx": slot_ctx or (props.get("default_generation_settings", {}).get("n_ctx")),
            "raw": metrics
        }

    def get_log_path(self) -> Path | None:
        if self.active_model is None:
            return None

        return self.config.log_dir / f"{self.active_model}.log"