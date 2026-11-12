import re
import tomllib

from dataclasses import dataclass
from pathlib     import Path
from typing      import Any


ROOT_DIR    = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT_DIR / "config.toml"

_QUANT_PATTERN = re.compile(
    r"(?:^|[-_.])((?:I?Q[1-8](?:_K_[SML]|_[01K]|_X[SL]|_NL)?)|BF16|F16|F32|MXFP4)(?=$|[-_.])",
    re.IGNORECASE,
)
_SPLIT_SHARD_PATTERN = re.compile(r"-(\d{5})-of-(\d{5})\.gguf$", re.IGNORECASE)


class ConfigError(Exception):
    """Raised when PAIN configuration is invalid"""
    ...


@dataclass
class ModelConfig:
    name : str
    file : str

    role         : str
    quantization : str
    personality  : str
    context_size : int

    model_path       : Path
    personality_path : Path

    size_bytes : int = 0
    available  : bool = True


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

    flash_attn   : str = "on"
    cache_type_k : str = "q8_0"
    cache_type_v : str = "q8_0"


@dataclass
class ResourceConfig:
    max_concurrent_inference : int
    max_concurrent_tasks     : int


class Config:
    DEFAULT_CONTEXT_SIZE = 16384
    DEFAULT_PERSONALITY = "PAIN.txt"

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

    @staticmethod
    def _extract_quantization(filename: str) -> str:
        stem = Path(filename).stem
        match = _QUANT_PATTERN.search(stem)
        return match.group(1).upper() if match else ""

    @staticmethod
    def _clean_display_name(path: Path, quant: str) -> str:
        name = path.stem
        name = re.sub(r"-\d{5}-of-\d{5}$", "", name)
        if quant:
            name = re.sub(rf"[-_.]{re.escape(quant)}$", "", name, flags=re.IGNORECASE)
        name = re.sub(r"[-_.]gguf$", "", name, flags=re.IGNORECASE)
        return name.strip("-_ .") or path.stem

    @staticmethod
    def _infer_role(filename: str) -> str:
        lower = filename.lower()
        if "coder" in lower or "code" in lower:
            return "Coding"
        if "gemma" in lower:
            return "Study"
        if "lfm" in lower:
            return "Research agent"
        if "granite" in lower:
            return "Agent/tool-use alternative"
        if "qwen3.5" in lower or "qwen3" in lower:
            return "Research analysis"
        if "math" in lower:
            return "Reasoning & math"
        return "Local inference"

    @staticmethod
    def _is_primary_gguf(path: Path) -> bool:
        if not path.is_file() or path.suffix.lower() != ".gguf":
            return False
        name_lower = path.name.lower()
        if "mmproj" in name_lower:
            return False
        shard = _SPLIT_SHARD_PATTERN.search(name_lower)
        if shard and int(shard.group(1)) != 1:
            return False
        return True

    def _resolve_personality(self, personality_name: str | None) -> tuple[str, Path]:
        requested = (personality_name or self.DEFAULT_PERSONALITY).strip()
        candidate = self.personality_dir / requested
        if candidate.is_file():
            return requested, candidate

        fallback = self.personality_dir / self.DEFAULT_PERSONALITY
        if fallback.is_file():
            return self.DEFAULT_PERSONALITY, fallback

        if self.personality_dir.is_dir():
            first_txt = next(iter(sorted(self.personality_dir.glob("*.txt"))), None)
            if first_txt is not None:
                return first_txt.name, first_txt

        return requested, candidate

    def discover_models(self) -> dict[str, ModelConfig]:
        configured_raw = self.raw.get("models", {})

        # Collect primary GGUF files from model_dir (including subdirectories for user-owned folders)
        discovered_files: list[Path] = []
        if self.model_dir.is_dir():
            discovered_files = sorted(
                (p for p in self.model_dir.rglob("*.gguf") if self._is_primary_gguf(p)),
                key=lambda p: (len(p.relative_to(self.model_dir).parts), p.name.lower()),
            )

        by_rel: dict[str, Path] = {}
        by_name: dict[str, Path] = {}
        for p in discovered_files:
            rel_key = p.relative_to(self.model_dir).as_posix().lower()
            by_rel[rel_key] = p
            by_name.setdefault(p.name.lower(), p)

        result: dict[str, ModelConfig] = {}
        claimed_paths: set[Path] = set()
        configured_entries: list[tuple[str, dict[str, Any]]] = list(configured_raw.items())

        # 1. Preserve configured metadata/ordering for models present in model_dir
        for name, cfg in configured_entries:
            model_file = self._require(cfg, "file", f"models.{name}")
            personality_name = cfg.get("personality", self.DEFAULT_PERSONALITY)
            context_size = cfg.get("context_size", self.DEFAULT_CONTEXT_SIZE)
            if not isinstance(context_size, int) or context_size <= 0:
                raise ConfigError("Model context_size must be a positive integer.")

            quant = str(cfg.get("quantization") or self._extract_quantization(model_file)).strip()
            role = str(cfg.get("role") or self._infer_role(model_file)).strip()
            p_name, p_path = self._resolve_personality(personality_name)

            direct_path = (self.model_dir / model_file)
            matched_path = None
            if direct_path.is_file():
                matched_path = direct_path
            else:
                rel_lookup = Path(model_file).as_posix().lower()
                matched_path = by_rel.get(rel_lookup) or by_name.get(Path(model_file).name.lower())

            if matched_path is not None:
                claimed_paths.add(matched_path)
                rel_file = matched_path.relative_to(self.model_dir).as_posix()
                result[name] = ModelConfig(
                    name=name,
                    file=rel_file,
                    role=role,
                    quantization=quant,
                    personality=p_name,
                    context_size=context_size,
                    model_path=matched_path,
                    personality_path=p_path,
                    size_bytes=matched_path.stat().st_size,
                    available=True,
                )

        # 2. Auto-register any additional GGUF files found in model_dir (e.g. another user's folder)
        for path in discovered_files:
            if path in claimed_paths:
                continue

            quant = self._extract_quantization(path.name)
            base_name = self._clean_display_name(path, quant)
            display_name = base_name
            counter = 2
            while display_name in result:
                display_name = f"{base_name} ({counter})"
                counter += 1

            p_name, p_path = self._resolve_personality(self.DEFAULT_PERSONALITY)
            rel_file = path.relative_to(self.model_dir).as_posix()

            result[display_name] = ModelConfig(
                name=display_name,
                file=rel_file,
                role=self._infer_role(path.name),
                quantization=quant,
                personality=p_name,
                context_size=self.DEFAULT_CONTEXT_SIZE,
                model_path=path,
                personality_path=p_path,
                size_bytes=path.stat().st_size,
                available=True,
            )

        # 3. Fallback when model_dir has no downloaded GGUFs yet: surface configured entries marked unavailable
        if not result and configured_entries:
            for name, cfg in configured_entries:
                model_file = self._require(cfg, "file", f"models.{name}")
                personality_name = cfg.get("personality", self.DEFAULT_PERSONALITY)
                context_size = cfg.get("context_size", self.DEFAULT_CONTEXT_SIZE)
                quant = str(cfg.get("quantization") or self._extract_quantization(model_file)).strip()
                role = str(cfg.get("role") or self._infer_role(model_file)).strip()
                p_name, p_path = self._resolve_personality(personality_name)
                result[name] = ModelConfig(
                    name=name,
                    file=model_file,
                    role=role,
                    quantization=quant,
                    personality=p_name,
                    context_size=context_size,
                    model_path=self.model_dir / model_file,
                    personality_path=p_path,
                    size_bytes=0,
                    available=False,
                )

        if not result:
            raise ConfigError(f"No models configured or found in {self.model_dir}")

        return result

    def _load_models(self):
        self.models = self.discover_models()

    def refresh_models(self) -> dict[str, ModelConfig]:
        self.models = self.discover_models()
        if (
            hasattr(self, "inference")
            and self.inference.default_model not in self.models
            and self.models
        ):
            self.inference.default_model = next(iter(self.models))
        return self.models

    def _load_inference(self):
        inference = self.raw.get("inference", {})

        flash_attn   = str(inference.get("flash_attn",   "on")).strip().lower()
        cache_type_k = str(inference.get("cache_type_k", "q8_0")).strip().lower()
        cache_type_v = str(inference.get("cache_type_v", "q8_0")).strip().lower()

        if flash_attn not in {"on", "off", "auto"}:
            raise ConfigError(
                f"Invalid [inference] flash_attn '{flash_attn}'. Expected: on, off, auto."
            )

        valid_kv_types = {"f32", "f16", "bf16", "q8_0", "q4_0", "q4_1", "iq4_nl", "q5_0", "q5_1"}
        if cache_type_k not in valid_kv_types:
            raise ConfigError(f"Invalid [inference] cache_type_k: {cache_type_k}")
        if cache_type_v not in valid_kv_types:
            raise ConfigError(f"Invalid [inference] cache_type_v: {cache_type_v}")
        if flash_attn == "off" and cache_type_v not in {"f32", "f16", "bf16"}:
            raise ConfigError("Quantized cache_type_v requires flash_attn enabled (on or auto).")

        configured_default = self._require(inference, "default_model", "inference")
        if configured_default not in self.models:
            # Automatically adapt if user points model_dir at their own local GGUFs
            configured_default = next(iter(self.models))

        self.inference = InferenceConfig(
            default_model = configured_default,

            threads     = self._require(inference, "threads",     "inference"),
            gpu_layers  = self._require(inference, "gpu_layers",  "inference"),
            max_tokens  = self._require(inference, "max_tokens",  "inference"),
            temperature = self._require(inference, "temperature", "inference"),

            top_p = self._require(inference, "top_p", "inference"),
            top_k = self._require(inference, "top_k", "inference"),
            min_p = self._require(inference, "min_p", "inference"),

            flash_attn   = flash_attn,
            cache_type_k = cache_type_k,
            cache_type_v = cache_type_v
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
        if name not in self.models:
            self.refresh_models()
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
