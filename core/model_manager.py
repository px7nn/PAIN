import atexit
import subprocess
import threading
import time

from pathlib        import Path
from urllib.request import urlopen
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

            "-np", "1",

            "--host", self.host,
            "--port", str(self.port),

            "--temp",  str(inference.temperature),
            "--top-p", str(inference.top_p),
            "--top-k", str(inference.top_k),
            "--min-p", str(inference.min_p),
            "--jinja",
            "--reasoning", "off",
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
        return {
            "running": self.is_running,
            "active_model": self.active_model,
            "server_url": self.base_url if self.is_running else None
        }

    def get_log_path(self) -> Path | None:
        if self.active_model is None:
            return None

        return self.config.log_dir / f"{self.active_model}.log"