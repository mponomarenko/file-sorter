"""OpenAI-compatible classifier for file categorization and folder actions.

Supports OpenAI API, Azure OpenAI, and any OpenAI-compatible endpoint (LM Studio, vLLM, etc.)
"""
import asyncio
import json
import logging
from pathlib import Path

import httpx

from ..categories import CategoryPath, UNKNOWN_CATEGORY
from ..config import config
from ..utils import log
from ..metrics import Metric
from ..folder_action import FolderAction, FolderActionRequest
from .base import Classifier, ClassifierResponse, FolderActionResponse


class OpenAIClassifier(Classifier):
    """OpenAI-compatible API classifier."""
    
    def __init__(
        self,
        url: str,
        model: str = "gpt-3.5-turbo",
        api_key: str | None = None,
        max_concurrency: int | None = None,
        file_prompt_template: str | Path | None = None,
        folder_prompt_template: str | Path | None = None,
        timeout: float = 120.0,
    ):
        if not url:
            raise ValueError("OpenAIClassifier requires a non-empty url")
        
        self.url = url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.sem = asyncio.Semaphore(max_concurrency or config.OLLAMA_WORKERS)
        
        # Setup HTTP client with auth
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        
        self.client = httpx.AsyncClient(timeout=timeout, headers=headers)
        self._logger = logging.getLogger("cleaner")
        
        # Default file prompt
        if file_prompt_template is None:
            file_prompt_template = Path(__file__).parent.parent.parent / "prompts" / "file_classification_default.prompt"
        
        self.file_prompt_template = self._load_prompt(file_prompt_template, "file_prompt")
        
        # Default folder prompt
        if folder_prompt_template is None:
            folder_prompt_template = Path(__file__).parent.parent.parent / "prompts" / "folder_action_default.prompt"
        
        self.folder_prompt_template = self._load_prompt(folder_prompt_template, "folder_prompt")
    
    def _load_prompt(self, prompt: str | Path, prompt_type: str) -> str:
        """Load prompt from file or string.
        
        Args:
            prompt: Path to prompt file or prompt string
            prompt_type: Type of prompt for error messages (e.g., "file_prompt")
            
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
    
    def display_name(self) -> str:
        return f"openai({self.url}, {self.model})"
    
    def is_ai(self) -> bool:
        return True
    
    def _build_system_prompt(self) -> str:
        categories_json = config.categories.to_json(compact=True)
        template = self.file_prompt_template
        if "{categories_json}" in template:
            return template.replace("{categories_json}", categories_json)
        return f"{template}\n\nCategories JSON: {categories_json}"
    
    async def classify(
        self,
        name: str,
        rel_path: str,
        mime: str,
        sample: str,
        hint: dict | None = None,
    ) -> ClassifierResponse:
        if sample is None:
            sample = ""
        
        sys_prompt = self._build_system_prompt()
        context = hint or {}
        payload_sample = sample[: config.MAX_CONTENT_PEEK]
        source_path = context.get("source_path", rel_path)
        
        lines = [
            f"Filename: {name}",
            f"Path: {source_path}",
            f"MIME: {mime}",
        ]
        
        rule_hint_value = context.get("rule_category_path", context.get("rule_hint"))
        if rule_hint_value:
            lines.append(f"Rule Hint: {rule_hint_value}")
        
        lines.append("Content Sample:")
        lines.append(payload_sample or "")
        user_content = "\n".join(lines)
        
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_content},
            ],
            "temperature": 0,
        }
        
        async def _call() -> ClassifierResponse:
            try:
                log.debug(f"openai: POST classify name={name} rel={rel_path} mime={mime} model={self.model}")
                
                resp = await self.client.post(f"{self.url}/v1/chat/completions", json=payload)
                resp.raise_for_status()
                
                response_data = resp.json()
                content = response_data["choices"][0]["message"]["content"].strip()
                
                metrics = {
                    "raw_response": response_data,
                    "raw_output": content,
                    "model": self.model,
                    "endpoint": self.url,
                    "usage": response_data.get("usage", {}),
                }
                
                log.debug(f"openai: response classify name={name} -> {content}")
                
                # Parse answer
                answer_text = content
                if "Answer:" in content:
                    for line in content.splitlines():
                        if line.strip().lower().startswith("answer:"):
                            answer_text = line.split(":", 1)[1].strip()
                            break
                
                categories = config.categories
                normalized_path = categories.normalize_result(answer_text, None, fallback_text=answer_text)
                if normalized_path is None:
                    metrics["error"] = f"Unable to normalize response: {content!r}"
                    normalized_path = UNKNOWN_CATEGORY
                
                return ClassifierResponse(normalized_path, metrics)
                
            except Exception as exc:
                log.error(f"OpenAI classify error: {exc}")
                return ClassifierResponse(
                    UNKNOWN_CATEGORY,
                    metrics={"error": str(exc), "model": self.model, "endpoint": self.url},
                    error=exc,
                    error_context={"name": name, "rel_path": rel_path},
                )
        
        async with self.sem:
            metric = Metric()
            return await metric.timed_async(_call)
    
    def advise_folder_action(self, request: FolderActionRequest) -> FolderActionResponse:
        """AI classifier makes folder decision based on folder name and structure.
        
        AI classifiers always make final decisions (never delegate).
        Uses rule_hint as guidance if available.
        """
        
        if request.total_files == 0:
            return FolderActionResponse.decision(
                request.rule_hint or FolderAction.KEEP,
                reason="openai:empty_folder"
            )
        
        # Build prompt with optional hint
        hint_instruction = f" If uncertain, use the rule hint: {request.rule_hint}." if request.rule_hint else ""
        sys_prompt = self.folder_prompt_template + hint_instruction
        
        payload_dict = {
            "folder_name": request.folder_name,
            "folder_path": request.folder_path,
            "children": request.children,
            "total_files": request.total_files,
        }
        if request.rule_hint:
            payload_dict["rule_hint"] = str(request.rule_hint)
        
        api_payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": json.dumps(payload_dict)},
            ],
            "temperature": 0,
        }
        
        try:
            # Synchronous for now (could be async with sync wrapper)
            with httpx.Client(timeout=self.client.timeout, headers=dict(self.client.headers)) as client:
                resp = client.post(f"{self.url}/v1/chat/completions", json=api_payload)
                resp.raise_for_status()
                
                response_data = resp.json()
                content = response_data["choices"][0]["message"]["content"].strip()
                
                log.info("openai_folder_response",
                        folder=request.folder_path,
                        raw_output=content,
                        model=self.model,
                        usage=response_data.get("usage", {}))
                
                try:
                    action = FolderAction.from_string(content)
                    return FolderActionResponse.decision(action, reason="openai:ai_decision")
                except ValueError:
                    log.warning("openai_folder_invalid_response",
                              folder=request.folder_path,
                              response=content,
                              using_hint=request.rule_hint)
                    return FolderActionResponse.decision(
                        request.rule_hint or FolderAction.DISAGGREGATE,
                        reason="openai:fallback_to_hint"
                    )
        
        except Exception as exc:
            log.warning(f"OpenAI folder advise failed: {exc}")
            return FolderActionResponse.decision(
                request.rule_hint or FolderAction.DISAGGREGATE,
                reason="openai:error"
            )
    
    def ensure_available(self) -> bool:
        """Check if OpenAI endpoint is available."""
        try:
            with httpx.Client(timeout=10.0, headers=dict(self.client.headers)) as client:
                # Try to list models
                log.debug(f"openai: checking {self.url}/v1/models for model {self.model}")
                resp = client.get(f"{self.url}/v1/models")
                resp.raise_for_status()
                
                data = resp.json()
                log.debug(f"openai: /v1/models response keys: {data.keys()}")
                
                model_ids = [m.get("id") for m in data.get("data", []) if isinstance(m, dict)]
                log.debug(f"openai: found {len(model_ids)} models at {self.url}: {model_ids}")
                
                if not model_ids:
                    log.debug(f"openai: no models available at {self.url}")
                    return False
                
                if self.model not in model_ids:
                    log.warning(f"openai: model '{self.model}' not found at {self.url}. Available: {model_ids[:5]}")
                    return False
                
                log.info(f"OpenAI endpoint OK at {self.url}, model: {self.model}")
                return True
                
        except Exception as e:
            log.debug(f"openai: endpoint check failed for {self.url}: {e}")
            return False
