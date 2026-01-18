import csv
import json
import re
from io import StringIO
from pathlib import Path, PurePath
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Set, Tuple

def _decompose_values(*values) -> list[str]:
    parts: list[str] = []
    for value in values:
        if value is None:
            continue
        if isinstance(value, CategoryPath):
            parts.extend([part for part in value.parts if part])
            continue
        if isinstance(value, PurePath):
            parts.extend([part for part in value.parts if part])
            continue
        text = str(value).strip()
        if not text:
            continue
        for seg in text.split("/"):
            cleaned = ''.join(ch for ch in seg.strip() if 32 <= ord(ch) <= 126)
            if cleaned:
                parts.append(cleaned)
    return parts


class CategoryPath:
    __slots__ = ("_parts",)

    def __init__(self, *segments):
        parts = _decompose_values(*segments)
        if not parts:
            parts = ["Unknown"]
        self._parts = tuple(parts)

    @property
    def parts(self) -> Tuple[str, ...]:
        return self._parts

    @property
    def label(self) -> str:
        return str(self)

    def __iter__(self):
        return iter(self._parts)

    def __len__(self) -> int:
        return len(self._parts)

    def __getitem__(self, index):
        return self._parts[index]

    def __str__(self) -> str:
        return "/".join(self._parts)

    def __repr__(self) -> str:
        return f"CategoryPath({str(self)!r})"

    def __hash__(self) -> int:
        return hash(self._parts)

    def __eq__(self, other) -> bool:
        if isinstance(other, CategoryPath):
            return self._parts == other._parts
        if isinstance(other, (tuple, list)):
            return self._parts == tuple(other)
        if isinstance(other, str):
            return str(self) == other
        return NotImplemented


UNKNOWN_CATEGORY = CategoryPath("Unknown")


def _normalize_tree(node, prefix: Tuple[str, ...], templates: Dict[Tuple[str, ...], str]) -> Dict[str, Dict]:
    if isinstance(node, list):
        normalized_list: Dict[str, Dict] = {}
        for entry in node:
            if isinstance(entry, str):
                normalized_list[entry] = {}
            elif isinstance(entry, Mapping):
                if len(entry) != 1:
                    raise ValueError("List entries must be strings or single-key objects")
                (child_key, child_value), = entry.items()
                key = str(child_key)
                normalized_list[key] = _normalize_tree(child_value, prefix + (key,), templates)
            else:
                raise ValueError(f"Unsupported category list entry type: {type(entry).__name__}")
        return normalized_list

    if not isinstance(node, Mapping):
        raise ValueError("Category tree nodes must be objects or arrays")

    normalized: Dict[str, Dict] = {}
    template_value = node.get("_template")
    if isinstance(template_value, str):
        templates[prefix] = template_value

    for raw_key, raw_value in node.items():
        if raw_key == "_template" or raw_key == "__default__":
            # Skip special keys - they're not categories
            continue
        key = str(raw_key)
        if isinstance(raw_value, str):
            templates[prefix + (key,)] = raw_value
            normalized[key] = {}
        else:
            normalized[key] = _normalize_tree(raw_value, prefix + (key,), templates)
    return normalized


def _parse_csv_categories(csv_content: str) -> Tuple[Dict[str, Dict], Dict[Tuple[str, ...], str]]:
    """Parse CSV format categories.
    
    Format: Category/Subcategory,template
    Lines starting with # are comments
    Empty lines are ignored
    """
    # Filter out comments and empty lines
    lines = []
    for line in csv_content.splitlines():
        line = line.strip()
        if line and not line.startswith('#'):
            lines.append(line)
    
    if not lines:
        return {}, {}
    
    # Parse CSV
    csv_io = StringIO('\n'.join(lines))
    reader = csv.reader(csv_io)
    
    # Build tree structure and collect templates
    tree: Dict[str, Dict] = {}
    templates: Dict[Tuple[str, ...], str] = {}
    
    for row in reader:
        if not row or not row[0]:
            continue
            
        category_path = row[0].strip()
        template = row[1].strip() if len(row) > 1 and row[1].strip() else None
        
        # Handle __default__ specially
        if category_path == "__default__":
            if template:
                templates[("__default__",)] = template
            continue
        
        # Split category path into parts
        parts = [p.strip() for p in category_path.split('/') if p.strip()]
        if not parts:
            continue
        
        # Build nested tree structure
        current = tree
        for part in parts[:-1]:
            if part not in current:
                current[part] = {}
            current = current[part]
        
        # Add final part
        final_part = parts[-1]
        if final_part not in current:
            current[final_part] = {}
        
        # Store template if provided
        if template:
            templates[tuple(parts)] = template
    
    return tree, templates


def _parse_categories(source) -> Tuple[Dict[str, Dict], Dict[Tuple[str, ...], str]]:
    if isinstance(source, Categories):
        return source.tree(), dict(source.templates)
    if isinstance(source, Path):
        raw = source.read_text(encoding="utf-8")
        return _parse_categories(raw)
    if isinstance(source, str):
        raw = source.strip()
        if not raw:
            return {}, {}
        
        # Detect format: CSV if starts with # or contains commas, otherwise JSON
        if raw.startswith('#') or (',' in raw and '{' not in raw[:100]):
            return _parse_csv_categories(raw)
        
        # Parse as JSON
        data: Mapping[str, object]
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            # If JSON parsing fails, try CSV
            return _parse_csv_categories(raw)
            
        if not isinstance(data, Mapping):
            raise ValueError(f"Categories JSON must decode to an object, got {type(data).__name__}")
        templates_json: Dict[Tuple[str, ...], str] = {}
        # Extract __default__ template if present
        if "__default__" in data and isinstance(data["__default__"], Mapping):
            default_template = data["__default__"].get("_template")
            if isinstance(default_template, str):
                templates_json[("__default__",)] = default_template
        normalized = _normalize_tree(data, (), templates_json)
        return normalized, templates_json
    if isinstance(source, Mapping):
        templates: Dict[Tuple[str, ...], str] = {}
        # Extract __default__ template if present
        if "__default__" in source and isinstance(source["__default__"], Mapping):
            default_template = source["__default__"].get("_template")
            if isinstance(default_template, str):
                templates[("__default__",)] = default_template
        normalized = _normalize_tree(source, (), templates)
        return normalized, templates
    raise TypeError(f"Unsupported categories source type: {type(source).__name__}")


def _compact_tree(node: Mapping[str, Dict], templates: Dict[Tuple[str, ...], str], prefix: Tuple[str, ...]) -> object:
    items = sorted(node.items(), key=lambda kv: kv[0])
    template = templates.get(prefix)
    if not items:
        return {}

    compact_children: list[tuple[str, object]] = []
    all_leaf = True
    for key, child in items:
        child_compact = _compact_tree(child, templates, prefix + (key,))
        compact_children.append((key, child_compact))
        if not (isinstance(child_compact, dict) and child_compact == {}):
            all_leaf = False

    if template is None and all_leaf:
        return [key for key, _ in compact_children]

    compact_dict: Dict[str, object] = {key: value for key, value in compact_children}
    return compact_dict

# TODO: this should not be read from json. lest make this also CSV with comments and everything
# nesting level is low enough, its easier to look at if its not nested tree, and we just repeat parent folders
class Categories:
    """Encapsulates category metadata, normalization, and validation helpers."""

    def __init__(self, tree: Mapping[str, Mapping], templates: Dict[Tuple[str, ...], str] | None = None):
        self._tree: Dict[str, Dict] = json.loads(json.dumps(tree))
        self._templates = templates or {}
        self._path_lookup: Dict[Tuple[str, ...], Tuple[str, ...]] = {}
        self._children: Dict[Tuple[str, ...], Set[str]] = {}
        self._index_tree(self._tree, ())
        self._children.setdefault((), set())
        compact = _compact_tree(self._tree, self._templates, ())
        self._compact_json = json.dumps(compact, separators=(",", ":"))

    @classmethod
    def from_source(cls, source) -> "Categories":
        if source is None:
            raise ValueError("Categories source is required")
        if isinstance(source, cls):
            return source
        tree, templates = _parse_categories(source)
        return cls(tree, templates)

    def categories(self) -> Iterable[str]:
        return list(self._children.get((), set()))

    def normalize(self, value, *extra) -> Optional[CategoryPath]:
        parts = self._decompose(value, *extra)
        if not parts:
            return None
        canonical = self._lookup(parts)
        if canonical is None:
            return None
        return CategoryPath(*canonical)

    def normalize_path(self, value) -> Optional[CategoryPath]:
        parts = self._decompose(value)
        if not parts:
            return None
        canonical = self._lookup(parts)
        if canonical is None:
            return None
        return CategoryPath(*canonical)

    def normalize_result(
        self,
        value,
        *extra,
        fallback_text: Optional[str] = None,
    ) -> CategoryPath:
        path = self.normalize(value, *extra)
        if path is None and fallback_text:
            path = self.find_in_text(fallback_text)
        if path is None:
            return UNKNOWN_CATEGORY
        return path

    def find_in_text(self, text: Optional[str]) -> Optional[CategoryPath]:
        if not text:
            return None
        low = text.lower()
        best: Optional[Tuple[str, ...]] = None
        best_len = 0
        for norm, canonical in self._path_lookup.items():
            if all(part in low for part in norm):
                if len(canonical) > best_len:
                    best = canonical
                    best_len = len(canonical)
        if best is None:
            return None
        return CategoryPath(*best)

    def flattened(self) -> Dict[str, Set[str]]:
        out: Dict[str, Set[str]] = {}
        for cat in self._children.get((), set()):
            child_key = (cat,)
            out[cat] = set(self._children.get(child_key, set()))
        return out

    def flattened_lists(self) -> Dict[str, list[str]]:
        return {k: sorted(v) for k, v in self.flattened().items()}

    def tree(self) -> Dict[str, Dict]:
        return json.loads(json.dumps(self._tree))

    def to_json(self, *, compact: bool = True) -> str:
        if compact:
            return self._compact_json
        return json.dumps(self._tree, indent=2)

    @property
    def templates(self) -> Dict[Tuple[str, ...], str]:
        return dict(self._templates)

    def template_for(self, path: CategoryPath) -> Optional[str]:
        key = tuple(path.parts)
        # First try to get specific template for this path
        template = self._templates.get(key)
        if template is not None:
            return template
        # Fall back to __default__ template if available
        return self._templates.get(("__default__",))

    def _index_tree(self, tree: Mapping[str, Mapping], prefix: Tuple[str, ...]) -> None:
        children: Set[str] = set()
        for key, value in tree.items():
            canonical = prefix + (key,)
            norm = tuple(part.lower() for part in canonical)
            self._path_lookup[norm] = canonical
            children.add(key)
            if isinstance(value, Mapping):
                self._index_tree(value, canonical)
        self._children[prefix] = children

    def _lookup(self, parts: Sequence[str]) -> Optional[Tuple[str, ...]]:
        norm = tuple(part.lower() for part in parts)
        canonical = self._path_lookup.get(norm)
        if canonical is not None:
            return canonical
        if len(parts) == 1:
            return self._path_lookup.get((norm[0],))
        for idx in range(len(parts) - 1, 0, -1):
            prefix = norm[:idx]
            canonical = self._path_lookup.get(prefix)
            if canonical:
                suffix = tuple(parts[idx:])
                return canonical + suffix
        return None

    def _tuple_key(self, value) -> Tuple[str, ...]:
        if isinstance(value, CategoryPath):
            return tuple(value.parts)
        if isinstance(value, (PurePath, Path)):
            return tuple(value.parts)
        parts = _decompose_values(value)
        return tuple(parts)

    def _decompose(self, *values) -> list[str]:
        return _decompose_values(*values)

    # ============================================================================
    # Template Rendering
    # ============================================================================
    
    _PLACEHOLDER_PATTERN = re.compile(r"\{([^{}]+)\}")
    
    def render_template(
        self,
        template: str,
        metadata: dict[str, Any],
        *,
        category_path: str | CategoryPath | None = None,
        kept_path: str | None = None,
        filename: str | None = None,
        sanitize: bool = True
    ) -> str:
        """Render a template string with metadata and auto-assemble full path.
        
        Templates should NEVER include {filename} or {suffix} - these are always auto-appended.
        Assembly order: Category/Template/KeptPath/Filename
        
        Special case: If template uses {title} in last segment, it replaces the filename
        (with extension preserved), so kept_path and filename are NOT appended.
        
        Args:
            template: Template string with {placeholder|fallback} syntax (metadata only)
            metadata: Dictionary with metadata fields (artist, album, year, etc.)
            category_path: Full category path to prepend (e.g., "Documents/Finance")
            kept_path: Kept folder path (suffix) - auto-added after template, before filename
            filename: Filename - auto-added at end (unless {title} renders it)
            sanitize: If True, sanitize path components (replace invalid chars)
            
        Returns:
            Fully assembled path: Category/Template/KeptPath/Filename
        """
        # Track which values were actually used in rendering to deduplicate suffix
        used_values: list[str] = []
        
        # Check if template includes filename in it
        template_lower = template.lower()
        has_filename_ref_in_template = any(ref in template_lower for ref in ["{filename}", "{file_stem}", "{title"])
        
        # Prepend category path if present
        segments: list[str] = []
        if category_path:
            # Add category parts (can be string like "Documents/Finance" or CategoryPath)
            if hasattr(category_path, 'parts'):
                segments.extend(category_path.parts)
            elif isinstance(category_path, str):
                segments.extend([p for p in category_path.split('/') if p])
        
        # Render template segments using metadata
        # Track whether we actually rendered a filename reference with content
        filename_rendered = False
        template_parts = template.strip("/").split("/")
        for idx, raw in enumerate(template_parts):
            is_last_part = (idx == len(template_parts) - 1)
            replaced = self._PLACEHOLDER_PATTERN.sub(
                lambda match: self._resolve_placeholder(match.group(1), metadata, used_values),
                raw,
            )
            
            # If this is the last template part and contains {title}, it's meant to be the filename
            # Add extension from original filename
            if is_last_part and has_filename_ref_in_template and replaced and filename:
                # Extract extension from original filename
                import pathlib
                ext = pathlib.Path(filename).suffix
                if ext and not replaced.endswith(ext):
                    replaced = replaced + ext
                filename_rendered = True  # We actually rendered a filename
            
            for part in replaced.split("/"):
                if sanitize:
                    part = self._sanitize_component(part)
                if part:
                    segments.append(part)
        
        # Add kept path (suffix) if present - these come after template but before filename
        # But only if we didn't actually render a filename from the template
        if kept_path and not filename_rendered:
            # Deduplicate kept path against already-used values from template
            deduplicated_suffix = self._deduplicate_suffix(kept_path, used_values)
            if deduplicated_suffix:
                for part in str(deduplicated_suffix).split("/"):
                    if sanitize:
                        part = self._sanitize_component(part)
                    if part:
                        segments.append(part)
        
        # Add filename if we didn't render it from template and we have one
        if not filename_rendered and filename:
            fn = str(filename)
            if sanitize:
                fn = self._sanitize_component(fn)
            if fn:
                segments.append(fn)
        
        return "/".join(segments) if segments else ""
    
    def _resolve_placeholder(self, content: str, context: dict[str, Any], used_values: list[str]) -> str:
        """Resolve a placeholder like 'artist|Unknown Artist' to a value from context."""
        tokens = [token.strip() for token in content.split("|") if token.strip()]
        if not tokens:
            return ""
        total = len(tokens)
        for idx, token in enumerate(tokens):
            value = context.get(token)
            
            # Special handling for 'suffix' - deduplicate against already-used values
            if token == "suffix" and value and used_values:
                value = self._deduplicate_suffix(value, used_values)
            
            # Special handling for ai_category - strip redundant category prefix
            if token == "ai_category" and value:
                value = self._strip_category_prefix(value, context.get("category"))
            
            value = self._coerce_placeholder_value(value)
            if value is not None:
                resolved = str(value).strip()
                # Track this value as used (unless it's a suffix, which we already deduplicated)
                if token != "suffix" and resolved:
                    used_values.append(resolved)
                return resolved
            # Last token with multiple fallbacks - use as literal
            if idx == total - 1 and total > 1:
                return token
        return ""
    
    @staticmethod
    def _coerce_placeholder_value(value: Any) -> Any:
        """Coerce a value to a usable string, handling None, empty, lists, etc."""
        if value is None:
            return None
        if isinstance(value, (list, tuple, set)):
            for item in value:
                normalized = Categories._coerce_placeholder_value(item)
                if normalized not in (None, ""):
                    return normalized
            return None
        if isinstance(value, bytes):
            try:
                value = value.decode("utf-8", "ignore")
            except Exception:
                value = value.decode("latin-1", "ignore")
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            return text
        return value
    
    @staticmethod
    def _normalize_for_comparison(text: str) -> str:
        """Normalize text for fuzzy comparison - lowercase, remove whitespace, underscores, dashes."""
        if not text:
            return ""
        normalized = text.lower()
        normalized = re.sub(r'[\s_\-]+', '', normalized)
    
        return normalized
    @staticmethod
    def _strip_category_prefix(ai_category: Any, category: Any) -> str:
        """Strip redundant category prefix from ai_category.
        
        If ai_category is 'Documents/Finance' and category is 'Documents',
        return just 'Finance'. This prevents duplication in the final path.
        """
        if not ai_category:
            return ""
        
        ai_cat_str = str(ai_category).strip()
        if not category or not ai_cat_str:
            return ai_cat_str
        
        category_str = str(category).strip()
        # Check if ai_category starts with the category prefix
        if ai_cat_str.startswith(category_str + "/"):
            # Strip the prefix
            return ai_cat_str[len(category_str) + 1:]
        
        return ai_cat_str

    def _deduplicate_suffix(self, suffix_value: Any, used_values: list[str]) -> str:
        """Remove parts of suffix that fuzzy-match values already used in the template.
        
        This prevents duplication like: Books/Digital/Александра Маринина/Александра_Маринина/...
        when 'author' metadata and kept path both contain the author name.
        """
        if not suffix_value or not used_values:
            return str(suffix_value) if suffix_value else ""
        
        suffix_str = str(suffix_value)
        suffix_parts = [p for p in suffix_str.split("/") if p.strip()]
        
        # Normalize all used values for comparison
        normalized_used = {self._normalize_for_comparison(v) for v in used_values if v}
        
        # Filter out suffix parts that fuzzy-match any used value
        filtered_parts = []
        for part in suffix_parts:
            normalized_part = self._normalize_for_comparison(part)
            if normalized_part and normalized_part not in normalized_used:
                filtered_parts.append(part)
        
        return "/".join(filtered_parts)

    @staticmethod
    def _sanitize_component(value: Any) -> str:
        """Sanitize a path component by removing invalid filesystem characters."""
        if value is None:
            return ""
        text = str(value).strip()
        if not text:
            return ""
        text = text.replace("/", "_")
        text = re.sub(r"[<>:|?*]", "_", text)
        text = text.strip()
        return text


def load_categories(source) -> Dict[str, Set[str]]:
    return Categories.from_source(source).flattened()


def load_categories_tree(source) -> Mapping[str, Mapping]:
    return Categories.from_source(source).tree()
