import tomllib

from pathlib     import Path
from dataclasses import dataclass
from typing      import Any


ROOT_DIR    = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT_DIR / "config.toml"


class ConfigError(Exception):
    """Raised when PAIN configuration is invalid"""
    ...


@dataclass
class ModelConfig:
    name : str
    file : str
    
    personality  : str
    context_size : int
    
    model_path       : Path
    personality_path : Path
    

@dataclass
class InferenceConfig:
    default_model : str
    
    threads     : int
    gpu_layers  : int
    max_tokens  : int
    temperature : float
    
    top_p : float
    top_k : int
    min_p : float
    
    
@dataclass
class ResourceConfig:
    max_concurrent_inference : int
    max_concurrent_tasks     : int
    
    
class Config:
    def __init__(self):
        self.root_dir = ROOT_DIR
        
        if not CONFIG_PATH.is_file():
            raise ConfigError(f"Configuration not found: {CONFIG_PATH}")
        
        try:
            with CONFIG_PATH.open("rb") as f:
                self.raw: dict[str, Any] = tomllib.load(f)
                
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"Invalid TOML: {exc}") from exc
        
        self._load_app()
        self._load_paths()
        self._load_models()
        self._load_inference()
        self._load_resources()
       
        
    def _require(self, section: dict, key: str, section_name: str):
        if key not in section:
            raise ConfigError(f"Missing configuration: [{section_name}] {key}")
        return section[key]
    
    def _resolve(self, path: str) -> Path:
        p = Path(path).expanduser()
        
        if not p.is_absolute():
            p = self.root_dir / p
        
        return p.resolve()
    
    
    def _load_app(self):
        app = self.raw.get("app", {})
        
        self.name    = self._require(app, "name",    "app")
        self.version = self._require(app, "version", "app")
        self.debug   = app.get("debug", False)
        
    def _load_paths(self):
        paths = self.raw.get("paths", {})
        
        self.llama_dir       = self._resolve(self._require(paths, "llama_dir",   "paths"))
        self.model_dir       = self._resolve(self._require(paths, "model_dir",   "paths"))
        self.personality_dir = self._resolve(self._require(paths, "personality", "paths"))

        self.data_dir = self._resolve(paths.get("data_dir", "data"))
        self.log_dir  = self._resolve(paths.get("log_dir", "logs"))
        
    def _load_models(self):
        models = self.raw.get("models", {})

        if not models:
            raise ConfigError("No models configured")

        self.models: dict[str, ModelConfig] = {}

        for name, config in models.items():
            model_file   = self._require(config, "file",         f"models.{name}")
            personality  = self._require(config, "personality",  f"models.{name}")
            context_size = self._require(config, "context_size", f"models.{name}")

            self.models[name] = ModelConfig(
                name = name,
                file = model_file,
                
                personality  = personality,
                context_size = context_size,
                
                model_path       = self.model_dir       / model_file,
                personality_path = self.personality_dir / personality
            )

        if not all(
            isinstance(model.context_size, int) and model.context_size > 0
            for model in self.models.values()
        ):
            raise ConfigError("Model context_size must be a positive integer.")
    
    def _load_inference(self):
        inference = self.raw.get("inference", {})

        self.inference = InferenceConfig(
            default_model = self._require(inference, "default_model", "inference"),
            
            threads     = self._require(inference, "threads",     "inference"),
            gpu_layers  = self._require(inference, "gpu_layers",  "inference"),
            max_tokens  = self._require(inference, "max_tokens",  "inference"),
            temperature = self._require(inference, "temperature", "inference"),
            
            top_p = self._require(inference, "top_p", "inference"),
            top_k = self._require(inference, "top_k", "inference"),
            min_p = self._require(inference, "min_p", "inference")
        )

        if self.inference.default_model not in self.models:
            raise ConfigError(
                f"Unknown default model: {self.inference.default_model}"
            )
        
    def _load_resources(self):
        resources = self.raw.get("resources", {})

        self.resources = ResourceConfig(
            max_concurrent_inference = self._require(resources, "max_concurrent_inference", "resources"),
            max_concurrent_tasks     = self._require(resources, "max_concurrent_tasks",     "resources")
        )

        if (
            self.resources.max_concurrent_inference != 1
            or self.resources.max_concurrent_tasks < 1
        ):
            raise ConfigError("Invalid resource concurrency configuration.")
        
    
    def get_model(self, name: str) -> ModelConfig:
        try:
            return self.models[name]
        except KeyError:
            raise ConfigError(f"Unknown model: {name}") from None
    
    def validate_files(self):
        errors = []
        
        if not self.llama_dir.is_dir():
            errors.append(f"llama.cpp directory missing: {self.llama_dir}")
            
        server_path = self.llama_dir / "build/bin/llama-server"
        if not server_path.is_file():
            errors.append(f"llama-server executable missing: {server_path}")
        
        for model in self.models.values():
            if not model.model_path.is_file():
                errors.append(f"Model missing: {model.model_path}")

            if not model.personality_path.is_file():
                errors.append(f"Personality missing: {model.personality_path}")
        
        return errors
    

config = Config()