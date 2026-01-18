import asyncio
import logging
import json
from pathlib import Path

import httpx

from ..categories import CategoryPath, UNKNOWN_CATEGORY
from ..config import config
from ..utils import log
from ..metrics import Metric
from ..folder_action import FolderAction, FolderActionRequest
from .base import Classifier, ClassifierResponse, FolderActionResponse


class OllamaClassifier(Classifier):
    def __init__(
        self,
        url: str,
        model: str,
        max_concurrency: int | None = None,
        prompt_template: str | Path | None = None,
        folder_prompt_template: str | Path | None = None,
    ):
        if not url:
            raise ValueError("OllamaClassifier requires a non-empty url")
        if not model:
            raise ValueError("OllamaClassifier requires a model name")
        
        self.sem = asyncio.Semaphore(max_concurrency or config.OLLAMA_WORKERS)
        resolved = url
        if isinstance(resolved, (list, tuple)):
            resolved = resolved[0] if resolved else None
        if resolved and not str(resolved).startswith(("http://", "https://")):
            resolved = f"http://{resolved}"
        self.url = resolved
        self.model = model
        self.client = httpx.AsyncClient(timeout=config.OLLAMA_TIMEOUT)
        self.throttle_seconds = max(0.0, float(config.OLLAMA_THROTTLE_SECONDS))
        self._throttle_lock = asyncio.Lock()
        self._last_request_at: float = 0.0
        self._logger = logging.getLogger("cleaner")
        
        # File classification prompt - use default file if not provided
        if prompt_template is None:
            prompt_template = Path(__file__).parent.parent.parent / "prompts" / "file_classification_default.prompt"
        
        self.prompt_template = self._load_prompt(prompt_template, "file_prompt")
        
        # Folder action prompt - use default file if not provided
        if folder_prompt_template is None:
            folder_prompt_template = Path(__file__).parent.parent.parent / "prompts" / "folder_action_default.prompt"
        
        self.folder_prompt_template = self._load_prompt(folder_prompt_template, "folder_prompt")

    def _load_prompt(self, prompt: str | Path, prompt_type: str) -> str:
        """Load prompt from file or string.
        
        Args:
            prompt: Path to prompt file or prompt string
            prompt_type: Type of prompt for error messages
            
        Returns:
            Loaded prompt string
            
        Raises:
            FileNotFoundError: If prompt file doesn't exist
            Exception: If prompt cannot be loaded for any reason
        """
        if isinstance(prompt, Path) or (isinstance(prompt, str) and Path(prompt).exists()):
            path = Path(prompt)
            if not path.exists():
                raise FileNotFoundError(f"{prompt_type} file not found: {path}")
            return path.read_text().strip()
        
        return str(prompt).strip()

    async def close(self):
        await self.client.aclose()

    def _build_system_prompt(self) -> str:
        categories_json = config.categories.to_json(compact=True)
        template = self.prompt_template
        if "{categories_json}" in template:
            return template.replace("{categories_json}", categories_json)
        return f"{template}\n\nCategories JSON: {categories_json}"

    @staticmethod
    def _parse_answer(content: str) -> tuple[str | None, str | None]:
        if not content:
            return None, None
        answer: str | None = None
        reasoning_lines: list[str] = []
        for raw_line in content.splitlines():
            line = raw_line.strip()
            lower = line.lower()
            if lower.startswith("answer:"):
                possible = line.split(":", 1)[1].strip()
                if possible:
                    answer = possible
                continue
            if lower.startswith("thought:") or lower.startswith("reasoning:"):
                reasoning_lines.append(line)
        reasoning_text = "\n".join(reasoning_lines).strip()
        return answer, (reasoning_text or None)

    def _get_client(self) -> tuple[str, httpx.AsyncClient]:
        return self.url, self.client

    def display_name(self) -> str:
        return f"ollama({self.url})"

    def is_ai(self) -> bool:
        return True

    async def _enforce_throttle(self) -> float:
        if self.throttle_seconds <= 0:
            return 0.0
        async with self._throttle_lock:
            loop = asyncio.get_running_loop()
            now = loop.time()
            wait_time = self.throttle_seconds - (now - self._last_request_at)
            if wait_time > 0:
                await asyncio.sleep(wait_time)
                now = loop.time()
            self._last_request_at = now
            return max(0.0, wait_time)

    async def classify(self, name: str, rel_path: str, mime: str, sample: str, hint: dict | None = None) -> ClassifierResponse:
        if sample is None:
            sample = ""

        categories = config.categories
        sys_prompt = self._build_system_prompt()
        context = hint or {}
        payload_sample = sample[: config.MAX_CONTENT_PEEK]
        source_path = context.get("source_path", rel_path)
        effective_path = source_path
        lines: list[str] = [
            f"Filename: {name}",
            f"Path: {effective_path}",
            f"MIME: {mime}",
        ]
        rule_hint_value = context.get("rule_category_path", context.get("rule_hint"))
        if rule_hint_value:
            lines.append(f"Rule Hint: {rule_hint_value}")
        
        # Add metadata if available
        metadata = context.get("metadata")
        if metadata and isinstance(metadata, dict):
            metadata_lines = []
            for key, value in metadata.items():
                if value and key not in ("filename", "file_stem", "extension"):
                    metadata_lines.append(f"{key}: {value}")
            if metadata_lines:
                lines.append("Metadata:")
                lines.extend(f"  {line}" for line in metadata_lines[:10])
        
        lines.append("Content Sample:")
        lines.append(payload_sample or "")
        user_content = "\n".join(lines)

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_content},
            ],
            "stream": False,
            "options": {"temperature": 0},
        }
        retries = max(1, config.OLLAMA_RETRIES)
        backoff = config.OLLAMA_BACKOFF
        payload_json = json.dumps(payload)
        payload_size = len(payload_json)
        async def _call() -> ClassifierResponse:
            last_error: Exception | None = None
            last_metrics: dict = {}
            last_error_context: dict | None = None
            for attempt in range(retries):
                attempt_index = attempt + 1
                metrics: dict = {
                    "attempt": attempt_index,
                    "max_attempts": retries,
                }
                throttle_wait = await self._enforce_throttle()
                if throttle_wait > 0:
                    metrics["throttle_wait"] = throttle_wait
                url: str | None = None
                client: httpx.AsyncClient | None = None
                base_timeout = max(120, config.OLLAMA_TIMEOUT)
                timeout_s = base_timeout * attempt_index
                loop = asyncio.get_running_loop()
                started = loop.time()
                try:
                    url, client = self._get_client()
                    log.debug(
                        f"ollama: POST classify name={name} rel={rel_path} mime={mime} len={len(sample)} url={url}"
                    )
                    # Only log full prompts/payloads when DEBUG logging is enabled
                    if self._logger.isEnabledFor(logging.DEBUG):
                        log.debug(f"ollama: system prompt: {sys_prompt}")
                        log.debug(f"ollama: user payload:\n{user_content}")
                    resp = await client.post(f"{url}/api/chat", json=payload, timeout=timeout_s)
                    resp.raise_for_status()
                    finished = loop.time()
                    
                    if hasattr(client, "_failed"):
                        delattr(client, "_failed")
                        
                    response_data = resp.json()
                    content = response_data["message"]["content"].strip()
                    metrics.update(response_data.get("metrics", {}) or {})
                    metrics["raw_response"] = response_data
                    metrics["raw_output"] = content
                    answer_text, reasoning = self._parse_answer(content)
                    if reasoning:
                        metrics["reasoning"] = reasoning
                    metrics.update({
                        "total_duration": finished - started,
                        "prompt_size": payload_size,
                        "response_size": len(content),
                        "timeout_s": timeout_s,
                        "endpoint": url,
                    })
                    metrics.setdefault("ttft", metrics["total_duration"])
                    
                    log.debug(f"ollama: response classify name={name} -> {content}")
                    log.debug(f"ollama: metrics: {metrics}")
                    
                    target_text = answer_text or content
                    normalized_path = categories.normalize_result(target_text, None, fallback_text=target_text)
                    if normalized_path is None:
                        metrics["error"] = f"Unable to normalize response: {content!r}"
                        normalized_path = UNKNOWN_CATEGORY
                    return ClassifierResponse(normalized_path, metrics)
                except Exception as exc:
                    last_error = exc
                    metrics["error"] = str(exc)
                    metrics["timeout_s"] = timeout_s
                    if url:
                        metrics["endpoint"] = url
                    metrics["total_duration"] = loop.time() - started
                    metrics.setdefault("ttft", metrics["total_duration"])
                    last_metrics = metrics
                    last_error_context = {"attempt": attempt_index, "url": url}
                    if client is not None:
                        client._failed = True  # type: ignore[attr-defined]
                    wait = backoff ** attempt
                    if wait < self.throttle_seconds:
                        wait = self.throttle_seconds
                    if attempt < retries - 1:
                        log.warning(
                            f"Ollama error (attempt {attempt_index}/{retries}, url={url}): {exc}; retrying in {wait:.1f}s"
                        )
                        await asyncio.sleep(wait)
                        continue
                    log.warning(f"Ollama classify failed after {retries} attempts: {exc}")
            # Ensure we always return a ClassifierResponse with metrics
            return ClassifierResponse(
                UNKNOWN_CATEGORY,
                metrics=last_metrics,
                error=last_error,
                error_context=last_error_context,
            )

        async with self.sem:
            metric = Metric()
            return await metric.timed_async(_call)

    def ensure_available(self) -> bool:
        """Check if Ollama endpoint is available."""
        try:
            with httpx.Client(timeout=config.OLLAMA_TIMEOUT) as c:
                r = c.get(f"{self.url}/api/version")
                r.raise_for_status()
                version_data = r.json()
                # LM Studio returns 200 with error field for unsupported endpoints
                if isinstance(version_data, dict) and "error" in version_data:
                    log.debug(f"Ollama API not supported at {self.url}: {version_data.get('error')}")
                    return False
                
                r = c.get(f"{self.url}/api/tags")
                r.raise_for_status()
                data = r.json()
                # Check for error field in tags response too
                if isinstance(data, dict) and "error" in data:
                    log.debug(f"Ollama API not supported at {self.url}: {data.get('error')}")
                    return False
                
                if isinstance(data, dict) and "models" in data:
                    models = [m.get("name") or m.get("model") for m in data.get("models", [])]
                elif isinstance(data, list):
                    models = [m.get("name") or m.get("model") for m in data]
                else:
                    models = []
                models = [m for m in models if isinstance(m, str)]
                want = self.model
                found = any(m == want or (m and m.startswith(want + ":")) for m in models)
                if not found:
                    log.warning(
                        f"Ollama up but model not installed at {self.url}: want='{want}', have={models}"
                    )
                    return False
                warm_payload = {
                    "model": want,
                    "messages": [
                        {"role": "system", "content": "Reply with strictly the word ok."},
                        {"role": "user", "content": "hello"},
                    ],
                    "stream": False,
                    "options": {"temperature": 0},
                }
                warm_resp = c.post(f"{self.url}/api/chat", json=warm_payload)
                warm_resp.raise_for_status()
                log.info(f"Ollama OK at {self.url}; model available: {want}")
                return True
        except Exception as e:
            log.error(f"Ollama check failed for {self.url}: {e}")
            return False

    def advise_folder_action(self, request: FolderActionRequest) -> FolderActionResponse:
        """AI classifier makes folder decision based on folder name and structure.
        
        AI classifiers always make final decisions (never delegate).
        Uses rule_hint as guidance if available.
        """
        import time
        
        if request.total_files == 0:
            return FolderActionResponse.decision(
                request.rule_hint or FolderAction.KEEP,
                reason="ollama:empty_folder"
            )

        hint_instruction = f" If uncertain, use the rule hint: {request.rule_hint}." if request.rule_hint else ""
        sys_prompt = self.folder_prompt_template + hint_instruction
        
        payload = {
            "folder_name": request.folder_name,
            "folder_path": request.folder_path,
            "children": request.children,
            "total_files": request.total_files,
        }
        if request.rule_hint:
            payload["rule_hint"] = str(request.rule_hint)
        try:
            with httpx.Client(timeout=config.OLLAMA_TIMEOUT) as c:
                for attempt in range(config.OLLAMA_RETRIES):
                    try:
                        r = c.post(
                            f"{self.url}/api/chat",
                            json={
                                "model": self.model,
                                "messages": [
                                    {"role": "system", "content": sys_prompt},
                                    {"role": "user", "content": json.dumps(payload)},
                                ],
                                "stream": False,
                                "options": {"temperature": 0},
                            },
                        )
                        r.raise_for_status()
                        resp_json = r.json()
                        out = resp_json.get("message", {}).get("content", "").strip()
                        log.info("ollama_folder_response", 
                                folder=request.folder_path,
                                raw_output=out,
                                payload_sent=payload,
                                full_response=resp_json)
                        
                        try:
                            action = FolderAction.from_string(out)
                            return FolderActionResponse.decision(action, reason="ollama:ai_decision")
                        except ValueError:
                            log.warning("ollama_folder_invalid_response",
                                      folder=request.folder_path,
                                      response=out,
                                      using_hint=request.rule_hint)
                            return FolderActionResponse.decision(
                                request.rule_hint or FolderAction.DISAGGREGATE,
                                reason="ollama:fallback_to_hint"
                            )
                    except Exception as e:
                        if attempt < config.OLLAMA_RETRIES - 1:
                            import random
                            wait = (config.OLLAMA_BACKOFF ** attempt) + random.uniform(0, 0.25)
                            log.warning(
                                f"Ollama folder advise attempt {attempt+1}/{config.OLLAMA_RETRIES} failed: {e}; retrying in {wait:.1f}s"
                            )
                            time.sleep(wait)
                            continue
                        log.warning(
                            f"Ollama folder advise failed after {config.OLLAMA_RETRIES} attempts: {e}"
                        )
                return FolderActionResponse.decision(
                    request.rule_hint or FolderAction.DISAGGREGATE,
                    reason="ollama:error_fallback"
                )
        except Exception as e:
            log.debug(f"advise_folder_action: httpx/unavailable: {e}")
            return FolderActionResponse.decision(
                request.rule_hint or FolderAction.DISAGGREGATE,
                reason="ollama:unavailable"
            )


# Module-level convenience for tests and callers
CATEGORIES = config.categories
